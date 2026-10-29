"""Real Ed25519 + process isolation, MODEL identities/inventory, not Windows proof."""
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from violet_office.accounting_access import FIELDS, ROLES
from violet_office.synthetic_recovery import SyntheticRealm, Scope, Denied, canonical
from tests.synthetic_separated_abort import (SeparatedLedger, SeparatedSupervisor,
    IsolatedIssuer, MockAuthenticatedPeer, _send, _receive)


def release_process(context, proof, go, ready, out, stop=False):
    ready.put(True)
    try:
        if not go.wait(10): raise RuntimeError('Barrier unavailable')
        client=SeparatedLedger(context)
        client.complete_stop(proof) if stop else client.release_abort(proof)
        out.put(('stop' if stop else 'release','ok'))
    except Denied: out.put(('stop' if stop else 'release','denied'))


def restart_process(context, representative, token, out):
    client=SeparatedLedger(context)
    pending=client.authority._op('inspect',lambda state:token in state['permits'])
    try: client.complete_stop(representative); out.put((pending,True))
    except Denied: out.put((pending,False))


def disable_review_process(context, representative, identity, ready, out):
    ready.set()
    try:
        SeparatedLedger(context).disable_supervisor(representative,identity)
        out.put('disabled')
    except Exception as error:
        out.put(type(error).__name__)


class SeparatedAbortTests(unittest.TestCase):
    def setUp(self):
        self.realm=SyntheticRealm(); self.rep=self.realm.representative_token
        self.client=SeparatedLedger(self.realm.context)
        self.client.enroll(self.rep); self.client.open_after_review(self.rep,history_confirmed=True)
        self.scope=Scope('BIZ-A','AI27','TASK-A','detail',('amount_yen',),'2026-01-01','2026-02-01','PURPOSE')
        self.expires=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        self.client.propose(self.rep,'P1',self.scope,self.expires); self.client.approve(self.rep,'P1')
        self.supervisor=SeparatedSupervisor(self.client,self.rep,lambda p,s:True,timeout=1)

    def tearDown(self):
        self.supervisor.close(); self.realm.close()

    def job(self, mode='flush_fail'):
        return self.supervisor.start('P1',self.scope,lambda scope:[{'amount_yen':10}],mode=mode)

    def pending(self):
        return self.client.authority._op('inspect',lambda state:state['permits'])

    def proof(self):
        job=self.job(); return job,self.supervisor.abort_and_prove(job)

    def incomplete(self):
        self.client.request_stop(self.rep)
        with self.assertRaises(Denied): self.client.complete_stop(self.rep)

    def test_real_signature_releases_then_completes_stop(self):
        job,proof=self.proof()
        self.assertEqual(len(bytes.fromhex(proof['signature'])),64)
        self.assertFalse(job.process.is_alive())
        self.assertEqual(self.client.release_abort(proof)['outcome'],'separated_abort_verified')
        self.client.complete_stop(self.rep)

    def test_private_key_is_not_exported_or_written_to_realm(self):
        _,proof=self.proof()
        self.assertEqual(set(self.supervisor.issuer.registration),{'public','key_id','boot'})
        self.assertFalse(hasattr(self.supervisor.issuer,'_secret'))
        self.assertEqual(list((Path(self.realm.context.root)/'mock-external').glob('supervisor-*.key')),[])
        self.assertNotIn('private',canonical(proof))
        with self.assertRaises(Denied): self.supervisor.issuer.request(self.supervisor.peer,'export')
        self.incomplete()

    def test_worker_forged_signature_denied(self):
        job,proof=self.proof(); proof['signature']='00'*64
        with self.assertRaises(Denied): self.client.release_abort(proof)
        self.assertIn(job.token,self.pending()); self.incomplete()

    def test_attacker_private_key_signature_denied(self):
        _,proof=self.proof()
        signed={key:value for key,value in proof.items() if key!='signature'}
        proof['signature']=Ed25519PrivateKey.generate().sign(canonical(signed).encode()).hex()
        with self.assertRaises(Denied): self.client.release_abort(proof)
        self.incomplete()

    def test_forged_identity_object_denied_on_bound_ipc(self):
        with self.assertRaises(Denied):
            self.supervisor.issuer.request(MockAuthenticatedPeer('SUPERVISOR-SID'),'rotate')

    def test_worker_identity_denied_at_bootstrap(self):
        with self.assertRaises(Denied): IsolatedIssuer(MockAuthenticatedPeer('WORKER-SID'))

    def test_unauthenticated_remote_or_fake_server_denied(self):
        for peer in (MockAuthenticatedPeer('SUPERVISOR-SID',authenticated=False),
                     MockAuthenticatedPeer('SUPERVISOR-SID',remote=True),
                     MockAuthenticatedPeer('SUPERVISOR-SID',server='FAKE')):
            with self.subTest(peer=peer),self.assertRaises(Denied): IsolatedIssuer(peer)

    def test_other_process_channel_without_capability_is_denied(self):
        # A separate process is given the endpoint but not its bound capability.
        ctx=multiprocessing.get_context('spawn'); out=ctx.Queue()
        process=ctx.Process(target=bad_ipc_process,args=(self.supervisor.issuer._channel,out))
        try:
            process.start(); self.assertEqual(out.get(timeout=5),'unauthenticated')
            process.join(5); self.assertEqual(process.exitcode,0)
        finally:
            if process.is_alive(): process.terminate(); process.join(5)
            out.close(); out.join_thread()

    def test_issuer_crash_keeps_reservation(self):
        job=self.job()
        with self.assertRaises(Denied): self.supervisor.abort_and_prove(job,crash_before_proof=True)
        self.assertIn(job.token,self.pending()); self.incomplete()

    def test_issuer_external_termination_keeps_reservation(self):
        job=self.job(); self.supervisor.issuer.process.terminate(); self.supervisor.issuer.process.join(2)
        with self.assertRaises(Denied): self.supervisor.abort_and_prove(job)
        self.assertIn(job.token,self.pending()); self.incomplete()

    def test_private_key_loss_refuses_new_proof(self):
        job=self.job(); self.supervisor.issuer.request(self.supervisor.peer,'lose_key')
        with self.assertRaises(Denied): self.supervisor.abort_and_prove(job)
        self.assertIn(job.token,self.pending()); self.incomplete()

    def test_key_rotation_invalidates_old_proof(self):
        _,proof=self.proof(); self.supervisor.rotate_key(self.rep)
        with self.assertRaises(Denied): self.client.release_abort(proof)
        self.incomplete()

    def test_rotation_with_new_inspection_can_release(self):
        job=self.job(); self.supervisor.rotate_key(self.rep)
        proof=self.supervisor.abort_and_prove(job); self.client.release_abort(proof); self.client.complete_stop(self.rep)

    def test_public_key_substitution_in_proof_denied(self):
        _,proof=self.proof(); proof['public']='00'*32
        with self.assertRaises(Denied): self.client.release_abort(proof)
        self.incomplete()

    def test_unapproved_public_key_registry_update_denied(self):
        with self.assertRaises(Denied):
            self.client._change('not-representative','rotate_separated_issuer',identity=self.supervisor.identity,
                                registration={'public':'00'*32,'key_id':'ATTACK','boot':self.supervisor.issuer.boot})

    def test_consumed_proof_is_denied(self):
        _,proof=self.proof(); self.client.release_abort(proof)
        with self.assertRaises(Denied): self.client.release_abort(proof)
        self.client.complete_stop(self.rep)

    def test_expired_proof_denied(self):
        _,proof=self.proof(); current=datetime
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None): return current.now(tz)+timedelta(minutes=2)
        with patch('tests.synthetic_separated_abort.datetime',Clock):
            with self.assertRaises(Denied): self.client.release_abort(proof)
        self.incomplete()

    def test_other_generation_target_and_challenge_denied(self):
        _,proof=self.proof()
        for key in ('epoch','delivery','challenge'):
            bad=json.loads(canonical(proof)); bad['body']['intent'][key]='OTHER'
            with self.subTest(key=key),self.assertRaises(Denied): self.client.release_abort(bad)
        self.incomplete()

    def test_unknown_resources_block_issuer_request(self):
        job=self.job(); self.supervisor.unknown_resources.add('UNKNOWN-HANDLE')
        with patch.object(self.supervisor.issuer,'request',side_effect=AssertionError('Must not sign')):
            with self.assertRaises(Denied): self.supervisor.abort_and_prove(job)
        self.assertIn(job.token,self.pending()); self.incomplete()

    def test_worker_abnormal_exit_cannot_self_certify(self):
        job=self.job('crash_write')
        with self.assertRaises(Denied): self.supervisor.abort_and_prove(job)
        self.assertIn(job.token,self.pending()); self.incomplete()

    def test_worker_ack_without_observed_exit_cannot_issue(self):
        job=self.job()
        with patch.object(job.process,'is_alive',return_value=True):
            with self.assertRaises(Denied): self.supervisor.abort_and_prove(job)
        self.assertIn(job.token,self.pending()); self.incomplete()

    def test_supervisor_crash_cleanup_keeps_reservation(self):
        job=self.job(); self.supervisor.close()
        self.assertIn(job.token,self.pending()); self.incomplete()

    def test_restart_in_another_process_retains_unresolved_reservation(self):
        job=self.job(); self.supervisor.close()
        ctx=multiprocessing.get_context('spawn'); out=ctx.Queue()
        process=ctx.Process(target=restart_process,args=(self.realm.context,self.rep,job.token,out))
        try:
            process.start(); self.assertEqual(out.get(timeout=5),(True,False))
            process.join(5); self.assertEqual(process.exitcode,0)
        finally:
            if process.is_alive(): process.terminate(); process.join(5)
            out.close(); out.join_thread()

    def test_hmac_release_and_registration_are_explicitly_disabled(self):
        with self.assertRaises(Denied): self.client._change(self.rep,'register_abort_supervisor')
        with self.assertRaises(Denied): self.client._change(None,'release_verified_abort',evidence={})
        with self.assertRaises(Denied): self.client.release_abort({'body':{},'mac':'00'*32})

    def test_ai04_denied_before_fetch(self):
        fetched=[]
        with self.assertRaises(Denied):
            self.supervisor.start('P1',replace(self.scope,agent='AI04'),lambda scope:fetched.append(True))
        self.assertEqual(fetched,[])

    def test_business_isolation_and_normal_authorization(self):
        with self.assertRaises(Denied):
            self.supervisor.start('P1',replace(self.scope,business='BIZ-B'),lambda scope:{})
        with patch.object(self.supervisor,'normal',return_value=False):
            with self.assertRaises(Denied): self.job('normal')

    def test_all_28_roles_and_fields_maintained(self):
        for n in range(1,29):
            agent=f'AI{n:02}'
            for section in FIELDS:
                scope=replace(self.scope,agent=agent,section=section,fields=tuple(sorted(FIELDS[section])))
                with self.subTest(agent=agent,section=section):
                    if section not in ROLES.get(agent,set()):
                        with self.assertRaises(Denied): self.supervisor.start('UNKNOWN',scope,lambda s:{})
                    else:
                        policy=agent+section
                        self.client.propose(self.rep,policy,scope,self.expires); self.client.approve(self.rep,policy)
                        values={f:'2026-01-05' if f=='day' else 'revenue' if f=='kind' else 10 for f in scope.fields}
                        job=self.supervisor.start(policy,scope,lambda s:values)
                        self.assertEqual(job.status,'handed_off')

    def test_release_persistence_failure_denies_completion(self):
        _,proof=self.proof()
        from violet_office import synthetic_abort
        original=synthetic_abort.insert_record
        def fail_local(db,item):
            if db.execute('PRAGMA database_list').fetchone()[2]==str(self.realm.context.local):
                raise sqlite3.OperationalError('Synthetic write failure')
            return original(db,item)
        with patch.object(synthetic_abort,'insert_record',side_effect=fail_local):
            with self.assertRaises(Denied): self.client.release_abort(proof)
        with self.assertRaises(Denied): self.client.complete_stop(self.rep)

    def test_two_releases_and_stop_compete_safely(self):
        _,proof=self.proof(); self.client.request_stop(self.rep)
        ctx=multiprocessing.get_context('spawn'); go=ctx.Event(); ready,out=ctx.Queue(),ctx.Queue()
        processes=[ctx.Process(target=release_process,args=(self.realm.context,proof,go,ready,out)) for _ in range(2)]
        processes.append(ctx.Process(target=release_process,args=(self.realm.context,self.rep,go,ready,out,True)))
        try:
            for process in processes: process.start()
            for _ in processes: self.assertTrue(ready.get(timeout=10))
            go.set(); results=[out.get(timeout=10) for _ in processes]
            for process in processes: process.join(5); self.assertEqual(process.exitcode,0)
            self.assertEqual(results.count(('release','ok')),1)
            self.assertEqual(results.count(('release','denied')),1)
            if self.client.status()['mode']!='closed': self.client.complete_stop(self.rep)
            with self.assertRaises(Denied): self.job('normal')
        finally:
            go.set()
            for process in processes:
                if process.is_alive(): process.terminate(); process.join(5)
            ready.close(); out.close(); ready.join_thread(); out.join_thread()

    def test_signed_but_wrong_current_intent_is_denied(self):
        _,proof=self.proof()
        for key in ('epoch','delivery','challenge'):
            body=json.loads(canonical(proof['body'])); body['intent'][key]='OTHER'
            # Trusted signer compromise/bug can sign a wrong intent; verifier
            # must still compare it with its own current reservation.
            wrong=self.supervisor.issuer.request(self.supervisor.peer,'issue',body=body)
            with self.subTest(key=key),self.assertRaises(Denied): self.client.release_abort(wrong)
        self.incomplete()

    def test_generation_transition_invalidates_old_signature(self):
        _,proof=self.proof(); self.client.release_abort(proof); self.client.complete_stop(self.rep)
        self.client.new_recovery_generation(self.rep,history_confirmed=True)
        with self.assertRaises(Denied): self.client.release_abort(proof)
        self.client.open_after_review(self.rep,history_confirmed=True)
        with self.assertRaises(Denied): self.job('normal')

    def test_disabled_issuer_rejects_previously_valid_signature(self):
        _,proof=self.proof(); self.client.disable_supervisor(self.rep,self.supervisor.identity)
        with self.assertRaises(Denied): self.client.release_abort(proof)
        self.incomplete()

    def test_proof_does_not_contain_payload_or_ipc_secret(self):
        _,proof=self.proof(); self.client.release_abort(proof)
        for path in (self.realm.context.local,self.realm.context.external):
            with closing(sqlite3.connect(path)) as db:
                with closing(db.execute('SELECT body FROM journal')) as cursor:
                    text=str(cursor.fetchall())
            self.assertNotIn(self.supervisor.issuer._capability,text)
            self.assertNotIn('"amount_yen":10',text)
            self.assertNotIn('HTTP/1.1',text)

    def test_audit_connections_close_even_when_references_are_retained(self):
        # SQLite's context manager ends a transaction; it does not close the
        # connection. Retain references so GC cannot conceal a Windows lock.
        original=sqlite3.connect; retained=[]
        def connect(*args,**kwargs):
            db=original(*args,**kwargs)
            if args and isinstance(args[0],Path): retained.append(db)
            return db
        try:
            with patch.object(sqlite3,'connect',side_effect=connect):
                self.test_proof_does_not_contain_payload_or_ipc_secret()
            self.assertEqual(len(retained),2)
            for db in retained:
                with self.assertRaises(sqlite3.ProgrammingError): db.execute('SELECT 1')
        finally:
            # Test-owned inspection only; never release reservations for cleanup.
            for db in retained: db.close()

    def test_partial_write_buffer_abort_blocks_later_output(self):
        job=self.job('partial_fail')
        path=Path(self.realm.context.root)/'outputs'/(job.managed['job']+'.http')
        self.assertEqual(path.stat().st_size,7)
        proof=self.supervisor.abort_and_prove(job); size=path.stat().st_size
        self.client.release_abort(proof); self.client.complete_stop(self.rep)
        with self.assertRaises(Denied): self.supervisor.retry_for_test(job)
        self.assertEqual(path.stat().st_size,size)
        with self.assertRaises(Denied): self.job('normal')

    def test_review_real_legacy_hmac_proof_is_rejected(self):
        from violet_office.synthetic_abort import VerifiedLedger, OwnedOutputSupervisor
        old=SyntheticRealm(); supervisor=None
        try:
            client=VerifiedLedger(old.context); rep=old.representative_token
            client.enroll(rep); client.open_after_review(rep,history_confirmed=True)
            client.propose(rep,'OLD',self.scope,self.expires); client.approve(rep,'OLD')
            supervisor=OwnedOutputSupervisor(client,rep,lambda p,s:True,timeout=1)
            job=supervisor.start('OLD',self.scope,lambda scope:[{'amount_yen':10}],mode='flush_fail')
            actual_old_proof=supervisor.abort_and_prove(job)
            with self.assertRaises(Denied): self.client.release_abort(actual_old_proof)
            self.assertIn(job.token,client.authority._op('inspect',lambda state:state['permits']))
        finally:
            if supervisor is not None: supervisor.close()
            old.close()

    def test_review_hmac_knowledge_cannot_forge_new_active_worker_proof(self):
        from violet_office.synthetic_abort import FACTS
        from violet_office.synthetic_recovery import mac,digest
        from tests.synthetic_separated_abort import PROTOCOL
        job=self.job('pause_flush')
        intent=self.client.checked(lambda head:self.client.authority.request_abort(
            head,job.token,job.capability,self.supervisor.owner,job.operation))
        body={'protocol':PROTOCOL,'intent':intent,'facts':sorted(FACTS),
              'observation_hash':digest('FAKE'),'prior_output':'possible_or_unknown'}
        # Even a known fixture HMAC key does not produce an Ed25519 signature.
        signed={'algorithm':'Ed25519','key_id':self.supervisor.issuer.registration['key_id'],
                'issuer_boot':self.supervisor.issuer.boot,'body':body}
        wrong={**signed,'signature':mac('11'*32,signed)}
        with self.assertRaises(Denied): self.client.release_abort(wrong)
        self.assertTrue(job.process.is_alive()); self.assertIn(job.token,self.pending())
        self.incomplete()

    def test_review_signature_check_and_disable_are_serialized(self):
        import queue
        from tests import synthetic_separated_abort as module
        _,proof=self.proof(); real=module.Ed25519PublicKey
        ctx=multiprocessing.get_context('spawn'); ready=ctx.Event(); out=ctx.Queue()
        process=ctx.Process(target=disable_review_process,
            args=(self.realm.context,self.rep,self.supervisor.identity,ready,out))
        class ObservedPublicKey:
            @staticmethod
            def from_public_bytes(data):
                actual=real.from_public_bytes(data)
                class Verifier:
                    def verify(inner,signature,message):
                        actual.verify(signature,message)
                        process.start(); self.assertTrue(ready.wait(5))
                        with self.assertRaises(queue.Empty): out.get(timeout=0.2)
                return Verifier()
        try:
            with patch.object(module,'Ed25519PublicKey',ObservedPublicKey):
                self.client.release_abort(proof)
            self.assertEqual(out.get(timeout=10),'disabled')
            process.join(5); self.assertEqual(process.exitcode,0)
            with self.assertRaises(Denied): self.client.release_abort(proof)
            self.client.complete_stop(self.rep)
        finally:
            if process.pid is not None and process.is_alive(): process.terminate(); process.join(5)
            out.close(); out.join_thread()

    def test_review_external_write_failure_keeps_actual_permit(self):
        _,proof=self.proof()
        from violet_office import synthetic_abort
        token=proof['body']['intent']['delivery']
        with patch.object(synthetic_abort,'insert_record',side_effect=sqlite3.OperationalError('Synthetic external failure')):
            with self.assertRaises(Denied): self.client.release_abort(proof)
        self.assertIn(token,self.pending())
        with self.assertRaises(Denied): self.client.complete_stop(self.rep)

    def test_review_already_issued_true_proof_survives_issuer_exit_limit(self):
        job,proof=self.proof()
        self.assertFalse(job.process.is_alive())
        self.supervisor.issuer.process.terminate(); self.supervisor.issuer.process.join(2)
        # No live issuer-health check exists at release. A previously issued true
        # observation remains valid until expiry/key disable/generation change.
        self.client.release_abort(proof); self.client.complete_stop(self.rep)
        with self.assertRaises(Denied): self.job('normal')

    def test_review_replayed_ipc_sequence_is_rejected(self):
        issuer=self.supervisor.issuer
        _send(issuer._channel,{'command':'rotate','capability':issuer._capability,
                              'boot':issuer.boot,'sequence':0})
        self.assertTrue(issuer._channel.poll(2))
        self.assertEqual(_receive(issuer._channel),{'error':'unauthenticated'})
        issuer.request(self.supervisor.peer,'rotate')


def bad_ipc_process(connection, out):
    _send(connection,{'command':'issue','capability':'WRONG','boot':'WRONG','sequence':1,'body':{}})
    if connection.poll(5): out.put(_receive(connection).get('error'))
