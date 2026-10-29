"""Preconnection DESIGN models, not a deployable provider/Windows/abort API.

No Windows APIs, network, Office hooks, business DBs or production migration.
Signatures/kernel observations below are synthetic premises, not attestation.
Only disposable SQLite files model atomic proof release between processes.
"""
from contextlib import closing
from dataclasses import dataclass
import hashlib
import hmac
import json
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from violet_office.accounting_access import FIELDS, ROLES

TEST_KEY = b'public-fixture-key-not-a-real-signing-key'
REQUIRED_FACTS = frozenset({'writer_exit_observed','job_empty_observed',
    'all_handles_accounted','no_future_producer','pending_io_settled','private_buffers_unreachable'})


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def signed(value):
    return {'body':value,'mac':hmac.new(TEST_KEY,encoded(value),hashlib.sha256).hexdigest()}


def authentic(envelope):
    if not hmac.compare_digest(envelope['mac'],signed(envelope['body'])['mac']):
        raise PermissionError('Synthetic evidence invalid')
    return envelope['body']


def checkpoint(head, nonce, authority):
    return signed({'instance':'I1','head':head,'nonce':nonce,'authority':authority})


def agree(primary, witness, nonce, available=True):
    if available is not True:
        raise PermissionError('Synthetic trust source unavailable')
    a,b = authentic(primary),authentic(witness)
    if (a['nonce'] != nonce or b['nonce'] != nonce or a['instance'] != 'I1'
            or b['instance'] != 'I1' or a['authority'] != 'primary'
            or b['authority'] != 'witness' or a['head'] != b['head']):
        raise PermissionError('Synthetic trust sources disagree')
    return a['head']


@dataclass(frozen=True)
class MockKernelToken:
    sid: str
    logon: str = 'LOGON-1'
    authenticated: bool = True


def check_ipc(peer, service_sid, nonce, expected_nonce, claimed_role='viewer'):
    # The peer token represents a SUCCESSFUL kernel/SSPI result. Request fields
    # cannot create it in a real implementation. No impersonation occurs here.
    if (not peer.authenticated or peer.sid != 'SVC-GATE' or peer.logon != 'LOGON-1'
            or service_sid != 'SVC-ANCHOR' or nonce != expected_nonce):
        raise PermissionError('Synthetic IPC identity denied')
    return True  # claimed_role is deliberately not used to establish identity.


def representative(assertion, expected_nonce, scope_hash, now):
    a = authentic(assertion)
    if (a.get('subject') != 'REP-1' or a.get('issuer') != 'INDEPENDENT-IDP'
            or a.get('audience') != 'VIOLET-APPROVAL' or a.get('nonce') != expected_nonce
            or a.get('scope_hash') != scope_hash or a.get('mfa') is not True
            or a.get('expires',0) <= now):
        raise PermissionError('Synthetic representative confirmation denied')


def role_scope(agent, section, fields, business, assigned_business, active=True):
    if (active is not True or business != assigned_business
            or section not in ROLES.get(agent,set()) or not fields
            or not set(fields) <= FIELDS[section]):
        raise PermissionError('Synthetic role scope denied')


class AbortSpecModel:
    """Test-only specification of verified release. OS facts are mock inputs.

    Not connected to synthetic_recovery.MockAuthority. Its unresolved reservations
    remain unreleasable. This model defines a separately reviewed FUTURE protocol.
    """
    def __init__(self,path):
        self.path = Path(path)

    @classmethod
    def create(cls,path):
        path = Path(path)
        with path.open('xb'):
            pass
        with closing(sqlite3.connect(path,isolation_level=None)) as db:
            db.executescript('''
            CREATE TABLE state(id INTEGER PRIMARY KEY, mode TEXT NOT NULL);
            INSERT INTO state VALUES(1,'active');
            CREATE TABLE deliveries(id TEXT PRIMARY KEY, status TEXT NOT NULL,
                binding TEXT NOT NULL, proof_hash TEXT);
            CREATE TABLE audit(seq INTEGER PRIMARY KEY, action TEXT NOT NULL, ref TEXT NOT NULL);
            ''')
            binding={'instance':'I1','business':'BIZ-A','delivery':'D1','policy':'P1','epoch':2,
                'broker_boot':'BOOT-2','worker_birth':'PID-41-CREATION-7',
                'job':'JOB-ONE','handles_hash':'INVENTORY-1','nonce':'ABORT-NONCE-1',
                'operation':'ABORT-OP-1','head':[10,'HEAD-10']}
            db.execute('INSERT INTO deliveries VALUES(?,?,?,NULL)',('D1','unresolved',encoded(binding).decode()))
        return cls(path)

    def transact(self, fn):
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=rw',uri=True,
                                     isolation_level=None,timeout=5)) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                result=fn(db); db.execute('COMMIT'); return result
            except BaseException:
                db.execute('ROLLBACK'); raise

    def binding(self):
        return self.transact(lambda db:json.loads(db.execute('SELECT binding FROM deliveries WHERE id=?',('D1',)).fetchone()[0]))

    def proof(self, facts=REQUIRED_FACTS):
        return signed({**self.binding(),'supervisor':'REGISTERED-SUPERVISOR-1',
                       'facts':sorted(facts),'version':1})

    def request_stop(self):
        self.transact(lambda db:db.execute("UPDATE state SET mode='draining'"))

    def complete_stop(self):
        def op(db):
            if (db.execute('SELECT mode FROM state').fetchone()[0] != 'draining'
                    or db.execute("SELECT 1 FROM deliveries WHERE status!='aborted_verified'").fetchone()):
                raise PermissionError('Synthetic stop remains incomplete')
            db.execute("UPDATE state SET mode='closed'")
            db.execute('INSERT INTO audit(action,ref) VALUES(?,?)',('stop_complete','I1'))
        self.transact(op)

    def release(self, proof, primary, witness, *, available=True, local_confirmed=True, fail=False):
        body=authentic(proof)
        def op(db):
            status,binding,old_hash=db.execute('SELECT status,binding,proof_hash FROM deliveries WHERE id=?',('D1',)).fetchone()
            binding=json.loads(binding)
            if (body.get('version') != 1 or body.get('supervisor') != 'REGISTERED-SUPERVISOR-1'
                    or any(body.get(k) != v for k,v in binding.items())
                    or set(body.get('facts',[])) != REQUIRED_FACTS
                    or local_confirmed is not True
                    or agree(primary,witness,binding['nonce'],available) != binding['head']):
                raise PermissionError('Synthetic abort is not proven')
            proof_hash=hashlib.sha256(encoded(body)).hexdigest()
            if status=='aborted_verified':
                if old_hash==proof_hash:
                    return 'same_receipt'  # exact replay is idempotent, never a second release.
                raise PermissionError('Conflicting synthetic proof')
            if status!='unresolved':
                raise PermissionError('Unresolved synthetic delivery required')
            changed=db.execute("UPDATE deliveries SET status='aborted_verified',proof_hash=? WHERE id='D1' AND status='unresolved'",(proof_hash,))
            if changed.rowcount!=1:
                raise PermissionError('Synthetic release conflict')
            db.execute('INSERT INTO audit(action,ref) VALUES(?,?)',('abort_released','D1'))
            if fail:
                raise RuntimeError('Synthetic release audit/commit failure')
            return 'released'
        return self.transact(op)

    def audit(self):
        return self.transact(lambda db:db.execute('SELECT action,ref FROM audit ORDER BY seq').fetchall())


def release_worker(path, proof, a, b, ready, go, out):
    try:
        ready.put('ready')
        if not go.wait(10):
            raise RuntimeError('Synthetic release start timeout')
        out.put(AbortSpecModel(path).release(proof,a,b))
    except Exception as error:
        out.put(type(error).__name__)


class ExternalWindowsSpecificationTests(unittest.TestCase):
    def test_fresh_primary_and_independent_witness_agree(self):
        self.assertEqual(agree(checkpoint([10,'h10'],'n','primary'),checkpoint([10,'h10'],'n','witness'),'n'),[10,'h10'])

    def test_primary_admin_rollback_with_new_valid_response_is_detected(self):
        with self.assertRaises(PermissionError):
            agree(checkpoint([8,'h8'],'n','primary'),checkpoint([10,'h10'],'n','witness'),'n')

    def test_witness_rollback_or_disagreement_is_refused(self):
        with self.assertRaises(PermissionError):
            agree(checkpoint([10,'h10'],'n','primary'),checkpoint([9,'h9'],'n','witness'),'n')

    def test_old_authentic_response_cannot_answer_fresh_nonce(self):
        with self.assertRaises(PermissionError):
            agree(checkpoint([10,'h10'],'old','primary'),checkpoint([10,'h10'],'old','witness'),'new')

    def test_both_trust_domains_rolled_back_remain_a_limit(self):
        # There is no third truth source. This deliberately demonstrates the limit.
        self.assertEqual(agree(checkpoint([8,'h8'],'new','primary'),checkpoint([8,'h8'],'new','witness'),'new'),[8,'h8'])

    def test_offline_or_external_failure_denies(self):
        with self.assertRaises(PermissionError):
            agree(checkpoint([10,'h10'],'n','primary'),checkpoint([10,'h10'],'n','witness'),'n',False)

    def test_ipc_accepts_only_authenticated_kernel_peer_and_expected_service(self):
        self.assertTrue(check_ipc(MockKernelToken('SVC-GATE'),'SVC-ANCHOR','n','n'))
        for token,server,nonce in ((MockKernelToken('OTHER-USER'),'SVC-ANCHOR','n'),
                (MockKernelToken('SVC-GATE',authenticated=False),'SVC-ANCHOR','n'),
                (MockKernelToken('SVC-GATE',logon='OTHER-LOGON'),'SVC-ANCHOR','n'),
                (MockKernelToken('SVC-GATE'),'FAKE-SERVICE','n'),
                (MockKernelToken('SVC-GATE'),'SVC-ANCHOR','old')):
            with self.subTest(token=token,server=server),self.assertRaises(PermissionError):
                check_ipc(token,server,nonce,'n',claimed_role='representative')

    def test_representative_assertion_is_bound_to_scope_nonce_mfa_and_expiry(self):
        now=1000
        body={'subject':'REP-1','issuer':'INDEPENDENT-IDP','audience':'VIOLET-APPROVAL',
              'nonce':'n','scope_hash':'scope-1','mfa':True,'expires':1100}
        representative(signed(body),'n','scope-1',now)
        for bad in ({'subject':'AI04'},{'issuer':'UNKNOWN'},{'audience':'OTHER'},
                    {'nonce':'old'},{'scope_hash':'other'},{'mfa':False},{'expires':1000}):
            with self.subTest(bad=bad),self.assertRaises(PermissionError):
                representative(signed({**body,**bad}),'n','scope-1',now)

    def test_unsigned_or_forged_representative_is_denied(self):
        body={'subject':'REP-1','nonce':'n'}
        with self.assertRaises(PermissionError):
            representative({'body':body,'mac':'0'*64},'n','scope-1',1000)

    def test_all_28_roles_business_isolation_and_inactive_rejection(self):
        for number in range(1,29):
            agent=f'AI{number:02}'
            for section in FIELDS:
                with self.subTest(agent=agent,section=section):
                    if section in ROLES.get(agent,set()):
                        role_scope(agent,section,sorted(FIELDS[section]),'BIZ-A','BIZ-A')
                    else:
                        with self.assertRaises(PermissionError):
                            role_scope(agent,section,sorted(FIELDS[section]),'BIZ-A','BIZ-A')
        for agent,business,active in (('AI04','BIZ-A',True),('AI27','BIZ-B',True),('AI27','BIZ-A',False)):
            with self.assertRaises(PermissionError):
                role_scope(agent,'detail',['amount_yen'],business,'BIZ-A',active)


class VerifiedAbortSpecificationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.model=AbortSpecModel.create(Path(self.temp.name)/'specification.sqlite3')
        self.proof=self.model.proof()
        self.a=checkpoint([10,'HEAD-10'],'ABORT-NONCE-1','primary')
        self.b=checkpoint([10,'HEAD-10'],'ABORT-NONCE-1','witness')

    def tearDown(self):
        self.temp.cleanup()

    def release(self, proof=None, **kwargs):
        return self.model.release(proof or self.proof,self.a,self.b,**kwargs)

    def unresolved(self):
        self.model.request_stop()
        with self.assertRaises(PermissionError): self.model.complete_stop()
        self.assertNotIn(('abort_released','D1'),self.model.audit())

    def test_verified_abort_releases_then_allows_stop_completion(self):
        self.unresolved(); self.assertEqual(self.release(),'released')
        self.model.complete_stop()
        self.assertEqual(self.model.audit(),[('abort_released','D1'),('stop_complete','I1')])

    def test_missing_any_os_observation_keeps_delivery_unresolved(self):
        for fact in REQUIRED_FACTS:
            with self.subTest(fact=fact),self.assertRaises(PermissionError):
                self.release(self.model.proof(REQUIRED_FACTS-{fact}))
        self.unresolved()

    def test_close_timeout_or_exception_alone_cannot_prove_abort(self):
        for fact in ('close_returned','timeout','exception_raised','process_pid_missing'):
            with self.subTest(fact=fact),self.assertRaises(PermissionError):
                self.release(self.model.proof({fact}))
        self.unresolved()

    def test_wrong_generation_business_worker_or_operation_proof_is_denied(self):
        for key,value in (('epoch',1),('business','BIZ-B'),('worker_birth','PID-41-REUSED'),
                          ('broker_boot','OLD-BOOT'),('operation','OTHER-OP'),('delivery','D2'),
                          ('job','OTHER-JOB'),('handles_hash','INCOMPLETE'),('nonce','old')):
            body={**self.proof['body'],key:value}
            with self.subTest(key=key),self.assertRaises(PermissionError): self.release(signed(body))
        self.unresolved()

    def test_unregistered_supervisor_and_bad_signature_are_denied(self):
        with self.assertRaises(PermissionError):
            self.release(signed({**self.proof['body'],'supervisor':'USER-PROCESS'}))
        with self.assertRaises(PermissionError): self.release({**self.proof,'mac':'0'*64})
        self.unresolved()

    def test_external_disagreement_or_unconfirmed_local_commit_blocks_release(self):
        with self.assertRaises(PermissionError): self.release(local_confirmed=False)
        with self.assertRaises(PermissionError): self.release(available=False)
        with self.assertRaises(PermissionError):
            self.model.release(self.proof,self.a,checkpoint([9,'old'],'ABORT-NONCE-1','witness'))
        self.unresolved()

    def test_release_audit_failure_rolls_back_status_and_receipt(self):
        with self.assertRaises(RuntimeError): self.release(fail=True)
        self.unresolved()

    def test_supervisor_crash_before_proof_cannot_release(self):
        self.model.request_stop()
        restarted=AbortSpecModel(self.model.path)
        with self.assertRaises(PermissionError): restarted.complete_stop()
        self.assertEqual(restarted.audit(),[])

    def test_exact_duplicate_proof_is_idempotent_with_one_release_record(self):
        self.assertEqual(self.release(),'released')
        self.assertEqual(self.release(),'same_receipt')
        self.assertEqual(self.model.audit(),[('abort_released','D1')])

    def test_multiprocess_competing_release_records_only_one_transition(self):
        self.model.request_stop()
        ctx=multiprocessing.get_context('spawn'); ready,out=ctx.Queue(),ctx.Queue(); go=ctx.Event()
        workers=[ctx.Process(target=release_worker,args=(str(self.model.path),self.proof,self.a,self.b,ready,go,out)) for _ in range(2)]
        try:
            for worker in workers: worker.start()
            for _ in workers: self.assertEqual(ready.get(timeout=10),'ready')
            go.set(); self.assertCountEqual([out.get(timeout=10) for _ in workers],['released','same_receipt'])
            for worker in workers: worker.join(10); self.assertEqual(worker.exitcode,0)
            self.model.complete_stop()
            self.assertEqual(self.model.audit().count(('abort_released','D1')),1)
        finally:
            go.set()
            for worker in workers:
                if worker.is_alive(): worker.terminate(); worker.join(5)
            ready.close(); out.close(); ready.join_thread(); out.join_thread()

    def test_single_owner_buffer_abort_blocks_later_flush_close_and_resend(self):
        # A real BufferedWriter over a known synchronous mock raw sink. Remaining
        # Windows/job facts are separate TRUSTED MOCK observations, not inferred
        # merely from close(). No runtime abort API is implemented or connected.
        import io
        from tests import test_synthetic_recovery_output_failures as outputs
        raw=outputs.TransientRaw(); stream=io.BufferedWriter(raw,buffer_size=8192)
        try:
            stream.write(b'synthetic-private-buffer')
            with self.assertRaises(OSError): stream.flush()
            self.assertEqual(raw.received,b'')
            self.unresolved()
            raw.close()  # Fence the known raw sink BEFORE buffered cleanup.
            try: stream.close()
            except (OSError,ValueError): pass
            for operation in (stream.flush,lambda:stream.write(b'retry'),lambda:raw.write(b'retry')):
                with self.assertRaises((OSError,ValueError)): operation()
            self.assertEqual(raw.received,b'')
            self.assertEqual(self.release(),'released')
            self.model.complete_stop()
            self.assertEqual(raw.received,b'')
        finally: outputs.dispose_buffer(raw,stream)

    def test_failed_raw_abort_does_not_allow_stop_even_if_reflush_succeeds(self):
        import io
        from tests import test_synthetic_recovery_output_failures as outputs
        class AbortFailure(outputs.TransientRaw):
            def close(inner):
                raise OSError('Synthetic abort fence failed')
        raw=AbortFailure(); stream=io.BufferedWriter(raw,buffer_size=8192)
        try:
            stream.write(b'synthetic-private-buffer')
            with self.assertRaises(OSError): stream.flush()
            with self.assertRaises(OSError): raw.close()
            with self.assertRaises(PermissionError):
                self.release(self.model.proof(REQUIRED_FACTS-{'all_handles_accounted','no_future_producer'}))
            stream.flush()  # Delivery remains possible: abort has NOT been proven.
            self.assertTrue(raw.received)
            self.unresolved()
        finally:
            io.RawIOBase.close(raw)
            try: stream.close()
            except (OSError,ValueError): pass
