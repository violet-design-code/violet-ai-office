"""Owned synthetic abort tests. NOT Windows job/socket/IPC security guarantees."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from violet_office.accounting_access import FIELDS, ROLES
from violet_office.synthetic_recovery import SyntheticRealm, Scope, Denied, Unavailable, mac, canonical
from violet_office.synthetic_abort import OwnedOutputSupervisor, VerifiedLedger, FACTS


def release_worker(context, evidence, ready, go, out):
    try:
        ready.put('ready')
        if not go.wait(10): raise RuntimeError('Synthetic release barrier timeout')
        out.put(('ok',VerifiedLedger(context).release_abort(evidence)['receipt_id']))
    except Exception as error: out.put(('error',type(error).__name__))


def completion_worker(context, representative, ready, go, out):
    try:
        ready.put('ready')
        if not go.wait(10): raise RuntimeError('Synthetic completion barrier timeout')
        VerifiedLedger(context).complete_stop(representative); out.put(('stop','complete'))
    except Denied: out.put(('stop','pending'))
    except Exception as error: out.put(('error',type(error).__name__))


def review_disable_worker(context, representative, identity, ready, out):
    ready.set()
    try:
        VerifiedLedger(context).disable_supervisor(representative,identity)
        out.put('disabled')
    except Exception as error:
        out.put(type(error).__name__)


def review_restart_worker(context, token, representative, out):
    client=VerifiedLedger(context)
    pending=client.authority._op('review_restart',lambda state:token in state['permits'])
    try:
        client.complete_stop(representative)
        stopped=True
    except Denied:
        stopped=False
    out.put((pending,stopped))


class SyntheticAbortTests(unittest.TestCase):
    def setUp(self):
        self.realm=SyntheticRealm(); self.rep=self.realm.representative_token
        self.c=VerifiedLedger(self.realm.context)
        self.c.enroll(self.rep); self.c.open_after_review(self.rep,history_confirmed=True)
        self.scope=Scope('BIZ-A','AI27','TASK-A','detail',('amount_yen',),'2026-01-01','2026-02-01','PURPOSE-A')
        self.expires=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        self.policy='POLICY-A'; self.approve(self.policy,self.scope)
        self.supervisor=OwnedOutputSupervisor(self.c,self.rep,lambda p,s:True,timeout=1)
        self.other_supervisors=[]

    def tearDown(self):
        for supervisor in self.other_supervisors: supervisor.close()
        self.supervisor.close(); self.realm.close()

    def approve(self,policy,scope):
        self.c.propose(self.rep,policy,scope,self.expires); self.c.approve(self.rep,policy)

    def start(self, mode='flush_fail', policy=None, scope=None):
        return self.supervisor.start(policy or self.policy,scope or self.scope,lambda s:[{'amount_yen':10}],mode=mode)

    def pending(self):
        return self.c.authority._op('inspect_test',lambda s:dict(s['permits']))

    def cannot_complete(self):
        self.c.request_stop(self.rep)
        with self.assertRaises(Denied): self.c.complete_stop(self.rep)
        self.assertEqual(self.c.status()['mode'],'draining')

    def evidence(self, mode='flush_fail'):
        job=self.start(mode)
        self.assertEqual(job.status,'unresolved')
        envelope=self.supervisor.abort_and_prove(job)
        self.assertEqual(set(envelope['body']['facts']),FACTS)
        self.assertFalse(job.process.is_alive())
        self.assertEqual(job.process.exitcode,0)
        return job,envelope

    def test_owned_abort_requires_worker_ack_exit_and_releases_only_matching_reservation(self):
        job,evidence=self.evidence()
        self.cannot_complete()
        self.assertEqual(self.pending()[job.token]['phase'],'abort_requested')
        receipt=self.supervisor.release(evidence)
        self.assertEqual(receipt['outcome'],'abort_verified')
        self.assertEqual(receipt['delivery'],job.token)
        self.assertEqual(self.pending(),{})
        self.c.complete_stop(self.rep)

    def test_normal_owned_worker_closes_resources_and_hands_off(self):
        job=self.start('normal')
        self.assertEqual(job.status,'handed_off'); self.assertFalse(job.process.is_alive())
        self.assertEqual(self.pending(),{})
        path=Path(self.c.context.root)/'outputs'/(job.managed['job']+'.http')
        self.assertTrue(path.read_bytes().startswith(b'HTTP/1.1 200 OK'))
        self.c.request_stop(self.rep); self.c.complete_stop(self.rep)

    def test_partial_write_then_abort_fences_remaining_buffer(self):
        job=self.start('partial_fail'); self.assertEqual(job.status,'unresolved')
        path=Path(self.c.context.root)/'outputs'/(job.managed['job']+'.http')
        self.assertEqual(path.stat().st_size,7)
        proof=self.supervisor.abort_and_prove(job); size=path.stat().st_size
        self.supervisor.release(proof); self.c.complete_stop(self.rep)
        with self.assertRaises(Denied): self.supervisor.retry_for_test(job)
        self.assertEqual(path.stat().st_size,size)

    def test_reflush_before_abort_cannot_complete_stop(self):
        job=self.start(); self.cannot_complete()
        self.assertEqual(self.supervisor.retry_for_test(job),'retry_returned')
        self.cannot_complete()
        proof=self.supervisor.abort_and_prove(job)
        self.supervisor.release(proof); self.c.complete_stop(self.rep)

    def test_close_before_confirmed_abort_does_not_release(self):
        job=self.start(); self.cannot_complete()
        self.supervisor.retry_for_test(job,close=True)
        self.assertIn(job.token,self.pending())
        proof=self.supervisor.abort_and_prove(job)
        self.supervisor.release(proof); self.c.complete_stop(self.rep)

    def test_pause_during_output_then_stop_and_abort(self):
        job=self.start('pause_flush'); self.assertEqual(job.status,'emitting')
        self.cannot_complete()
        proof=self.supervisor.abort_and_prove(job)
        self.supervisor.release(proof); self.c.complete_stop(self.rep)

    def test_worker_abnormal_exit_without_abort_ack_is_insufficient(self):
        job=self.start('crash_write'); job.process.join(2)
        self.assertEqual(job.status,'unresolved'); self.assertNotEqual(job.process.exitcode,0)
        with self.assertRaises(Denied): self.supervisor.abort_and_prove(job)
        self.cannot_complete()

    def test_worker_closes_and_exits_without_abort_ack_stays_unresolved(self):
        job=self.start('abort_ack_missing')
        with self.assertRaises(Denied): self.supervisor.abort_and_prove(job)
        job.process.join(2); self.assertFalse(job.process.is_alive())
        self.cannot_complete()

    def test_abort_fence_failure_does_not_issue_evidence(self):
        job=self.start('abort_fence_fail')
        with self.assertRaises(Denied): self.supervisor.abort_and_prove(job)
        self.cannot_complete()

    def test_timeout_does_not_become_abort_confirmation(self):
        job=self.start('hang_abort')
        with self.assertRaises(Denied): self.supervisor.abort_and_prove(job)
        self.assertTrue(job.process.is_alive())
        self.cannot_complete()

    def test_supervisor_fault_before_proof_keeps_reservation(self):
        job=self.start()
        with self.assertRaises(Unavailable): self.supervisor.abort_and_prove(job,crash_before_proof=True)
        self.assertFalse(job.process.is_alive())
        with self.assertRaises(Unavailable): self.supervisor.abort_and_prove(job)
        self.cannot_complete()

    def test_supervisor_teardown_never_releases(self):
        job=self.start(); self.supervisor.close()
        self.assertIn(job.token,self.pending()); self.cannot_complete()

    def test_new_supervisor_cannot_claim_old_job(self):
        other=OwnedOutputSupervisor(self.c,self.rep,lambda p,s:True,timeout=1)
        self.other_supervisors.append(other)
        job=self.start()
        with self.assertRaises(Denied): other.abort_and_prove(job)
        self.cannot_complete()

    def test_forged_signature_and_supervisor_impersonation_denied(self):
        job,proof=self.evidence()
        for wrong in ({**proof,'mac':'0'*64},
                      {'body':{**proof['body'],'facts':[]},'mac':proof['mac']}):
            with self.assertRaises(Denied): self.c.release_abort(wrong)
        self.assertIn(job.token,self.pending()); self.cannot_complete()

    def test_valid_signature_with_incomplete_fact_set_denied(self):
        job,proof=self.evidence()
        body={**proof['body'],'facts':sorted(FACTS-{'owned_resources_closed'})}
        bad={'body':body,'mac':mac(self.supervisor._secret,body)}
        with self.assertRaises(Denied): self.c.release_abort(bad)
        self.assertIn(job.token,self.pending()); self.cannot_complete()

    def test_proof_for_other_delivery_or_scope_cannot_release(self):
        job,proof=self.evidence()
        for key,value in (('delivery','OTHER'),('epoch',0),('challenge','OLD')):
            body=json.loads(canonical(proof['body'])); body['intent'][key]=value
            envelope={'body':body,'mac':mac(self.supervisor._secret,body)}
            with self.subTest(key=key),self.assertRaises(Denied): self.c.release_abort(envelope)
        self.assertIn(job.token,self.pending()); self.cannot_complete()

    def test_expired_proof_does_not_remove_reservation(self):
        job,proof=self.evidence(); real_datetime=datetime
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None): return real_datetime.now(tz)+timedelta(minutes=2)
        with patch('violet_office.synthetic_abort.datetime',Clock):
            with self.assertRaises(Denied): self.c.release_abort(proof)
        self.assertIn(job.token,self.pending()); self.cannot_complete()

    def test_duplicate_exact_proof_returns_same_receipt_without_second_transition(self):
        job,proof=self.evidence()
        one=self.c.release_abort(proof); head=self.c.status()['head']
        two=self.c.release_abort(proof)
        self.assertEqual(one,two); self.assertEqual(head,self.c.status()['head'])
        self.assertEqual(self.pending(),{})

    def test_old_generation_proof_is_rejected_after_recovery(self):
        _,proof=self.evidence(); self.c.release_abort(proof); self.c.complete_stop(self.rep)
        self.c.new_recovery_generation(self.rep,history_confirmed=True)
        with self.assertRaises(Denied): self.c.release_abort(proof)
        self.c.open_after_review(self.rep,history_confirmed=True)
        with self.assertRaises(Denied): self.start('normal')

    def test_disable_supervisor_invalidates_unconsumed_proof(self):
        job,proof=self.evidence()
        self.c.disable_supervisor(self.rep,self.supervisor.identity)
        with self.assertRaises(Denied): self.c.release_abort(proof)
        self.assertIn(job.token,self.pending()); self.cannot_complete()

    def test_revocation_completion_waits_for_abort_receipt(self):
        job=self.start(); self.c.revoke(self.rep,self.policy)
        with self.assertRaises(Denied): self.c.complete_revocation(self.rep,self.policy)
        proof=self.supervisor.abort_and_prove(job); self.supervisor.release(proof)
        self.c.complete_revocation(self.rep,self.policy); self.c.complete_stop(self.rep)
        with self.assertRaises(Denied): self.start('normal')

    def test_stopped_gate_refuses_new_output_before_worker_creation(self):
        _,proof=self.evidence(); self.c.release_abort(proof); self.c.complete_stop(self.rep)
        before=set(self.supervisor.jobs)
        with self.assertRaises(Denied): self.start('normal')
        self.assertEqual(before,set(self.supervisor.jobs))

    def test_normal_authorization_and_ai04_denied_before_fetch(self):
        called=[]
        with self.assertRaises(Denied):
            self.supervisor.start(self.policy,replace(self.scope,agent='AI04'),lambda s:called.append(True))
        self.assertEqual(called,[])
        with patch.object(self.supervisor,'normal',return_value=False):
            with self.assertRaises(Denied): self.start('normal')
        self.assertEqual(self.supervisor.jobs,{})

    def test_business_isolation_remains_enforced(self):
        with self.assertRaises(Denied): self.start('normal',scope=replace(self.scope,business='BIZ-B'))
        self.assertEqual(self.supervisor.jobs,{})

    def test_old_local_domain_history_blocks_abort_release(self):
        from tests.test_synthetic_recovery import copy_image,restore_image
        local=copy_image(self.c.context.local)
        try:
            self.c.propose(self.rep,'NEW-PENDING',self.scope,self.expires)
            job,proof=self.evidence(); restore_image(local,self.c.context.local)
            with self.assertRaises(Denied): self.c.release_abort(proof)
            with self.assertRaises(Denied): self.c.complete_stop(self.rep)
        finally: local.close()

    def test_release_persistence_failure_keeps_gate_closed_to_completion(self):
        _,proof=self.evidence()
        from violet_office import synthetic_abort as module
        original=module.insert_record
        def fail_local(db,item):
            if db.execute('PRAGMA database_list').fetchone()[2]==str(self.c.context.local):
                raise sqlite3.OperationalError('Synthetic local write failure')
            return original(db,item)
        with patch.object(module,'insert_record',side_effect=fail_local):
            with self.assertRaises(Denied): self.c.release_abort(proof)
        with self.assertRaises(Denied): self.c.complete_stop(self.rep)
        with self.assertRaises(Denied): self.start('normal')

    def test_evidence_and_audit_do_not_contain_payload_or_signing_secret(self):
        _,proof=self.evidence(); self.c.release_abort(proof)
        text=canonical(proof)
        self.assertNotIn(self.supervisor._secret,text); self.assertNotIn('HTTP/1.1',text)
        for path in (self.c.context.local,self.c.context.external):
            with sqlite3.connect(path) as db: rows=str(db.execute('SELECT body FROM journal').fetchall())
            self.assertNotIn(self.supervisor._secret,rows)
            self.assertNotIn('"amount_yen":10',rows)

    def test_two_process_release_and_stop_race_is_safe_and_single_use(self):
        _,proof=self.evidence(); self.c.request_stop(self.rep)
        ctx=multiprocessing.get_context('spawn'); ready,out=ctx.Queue(),ctx.Queue(); go=ctx.Event()
        workers=[ctx.Process(target=release_worker,args=(self.c.context,proof,ready,go,out)) for _ in range(2)]
        workers.append(ctx.Process(target=completion_worker,args=(self.c.context,self.rep,ready,go,out)))
        try:
            for p in workers: p.start()
            for _ in workers: self.assertEqual(ready.get(timeout=10),'ready')
            go.set(); results=[out.get(timeout=15) for _ in workers]
            for p in workers: p.join(10); self.assertEqual(p.exitcode,0)
            self.assertEqual(len([r for r in results if r[0]=='ok']),2)
            self.assertEqual(len({r[1] for r in results if r[0]=='ok'}),1)
            self.assertTrue(any(r in (('stop','complete'),('stop','pending')) for r in results))
            self.assertFalse(any(r[0]=='error' for r in results))
            if self.c.status()['mode']!='closed': self.c.complete_stop(self.rep)
            self.assertEqual(self.pending(),{})
            with self.assertRaises(Denied): self.start('normal')
        finally:
            go.set()
            for p in workers:
                if p.is_alive(): p.terminate(); p.join(5)
            ready.close(); out.close(); ready.join_thread(); out.join_thread()

    def test_old_external_transport_history_blocks_abort_release(self):
        from tests.test_synthetic_recovery import copy_image,restore_image
        image=copy_image(self.c.context.external)
        try:
            _,proof=self.evidence(); restore_image(image,self.c.context.external)
            with self.assertRaises(Denied): self.c.release_abort(proof)
            with self.assertRaises(Denied): self.c.complete_stop(self.rep)
        finally: image.close()

    def test_ordinary_unmanaged_failure_cannot_obtain_owned_abort_challenge(self):
        guards=[(self.policy,self.scope.data())]
        token,cap=self.c.checked(lambda h:self.c.authority.reserve(h,guards,'ORDINARY','http'))
        self.c.checked(lambda h:self.c.authority.begin(h,token,cap,'ORDINARY'))
        self.c.checked(lambda h:self.c.authority.mark_unresolved(token,cap,'ORDINARY'))
        with self.assertRaises(Denied):
            self.c.checked(lambda h:self.c.authority.request_abort(h,token,cap,'ORDINARY','OP'))
        self.assertIn(token,self.pending()); self.cannot_complete()

    def test_all_28_roles_maintained_on_owned_output_path(self):
        for n in range(1,29):
            agent=f'AI{n:02}'
            for section in FIELDS:
                scope=replace(self.scope,agent=agent,task='TASK-'+agent,section=section,fields=tuple(sorted(FIELDS[section])))
                with self.subTest(agent=agent,section=section):
                    if section not in ROLES.get(agent,set()):
                        with self.assertRaises(Denied):
                            self.supervisor.start('UNKNOWN',scope,lambda s:{},mode='normal')
                    else:
                        policy=agent+'-'+section; self.approve(policy,scope)
                        values={f:'2026-01-05' if f=='day' else 'revenue' if f=='kind' else 10 for f in scope.fields}
                        job=self.supervisor.start(policy,scope,lambda s:values,mode='normal')
                        self.assertEqual(job.status,'handed_off')
        self.assertEqual(self.pending(),{})

    def test_supervisor_requires_representative_enrollment(self):
        with self.assertRaises(Denied):
            OwnedOutputSupervisor(self.c,'not-a-representative',lambda p,s:True)
        self.assertEqual(self.pending(),{})

    def test_incomplete_authenticated_worker_observation_cannot_issue_proof(self):
        job=self.start(); original=self.supervisor._command
        def incomplete(current,command,**args):
            observed=original(current,command,**args)
            if command=='abort': observed['pending_sync_io']=1
            return observed
        with patch.object(self.supervisor,'_command',side_effect=incomplete):
            with self.assertRaises(Denied): self.supervisor.abort_and_prove(job)
        self.assertIn(job.token,self.pending()); self.cannot_complete()

    def test_invalid_control_authentication_never_becomes_abort_proof(self):
        job=self.start(); job.control_key='00'*32
        with self.assertRaises(Denied): self.supervisor.abort_and_prove(job)
        self.assertIn(job.token,self.pending()); self.cannot_complete()

    def test_two_owned_jobs_require_two_independent_abort_receipts(self):
        one=self.start('pause_flush'); two=self.start('pause_flush')
        self.cannot_complete()
        first=self.supervisor.abort_and_prove(one)
        receipt=self.c.release_abort(first)
        self.assertIn(two.token,self.pending())
        self.assertEqual(self.c.release_abort(first),receipt)
        self.cannot_complete()
        second=self.supervisor.abort_and_prove(two)
        self.c.release_abort(second); self.c.complete_stop(self.rep)
        self.assertEqual(self.pending(),{})

    def test_replaced_challenge_refuses_old_unconsumed_proof(self):
        job,proof=self.evidence(); real_datetime=datetime
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None): return real_datetime.now(tz)+timedelta(minutes=2)
        with patch('violet_office.synthetic_abort.datetime',Clock):
            new=self.c.checked(lambda h:self.c.authority.request_abort(h,job.token,job.capability,
                                                                      self.supervisor.owner,job.operation))
        self.assertNotEqual(new['challenge'],proof['body']['intent']['challenge'])
        self.assertGreater(new['revision'],proof['body']['intent']['revision'])
        with self.assertRaises(Denied): self.c.release_abort(proof)
        self.assertIn(job.token,self.pending()); self.cannot_complete()

    def test_non_json_control_input_cannot_produce_abort_evidence(self):
        job=self.start('pause_flush')
        job.connection.send_bytes(b'not-json')
        job.process.join(2)
        self.assertFalse(job.process.is_alive())
        with self.assertRaises(Denied): self.supervisor.abort_and_prove(job)
        self.assertIn(job.token,self.pending()); self.cannot_complete()


    def test_review_restart_process_retains_unresolved_reservation(self):
        job=self.start(); self.supervisor.close()
        ctx=multiprocessing.get_context('spawn'); out=ctx.Queue()
        process=ctx.Process(target=review_restart_worker,args=(self.c.context,job.token,self.rep,out))
        try:
            process.start(); self.assertEqual(out.get(timeout=10),(True,False))
            process.join(5); self.assertEqual(process.exitcode,0)
        finally:
            if process.is_alive(): process.terminate(); process.join(5)
            out.close(); out.join_thread()

    def test_review_state_change_cannot_interleave_proof_check_and_release(self):
        import queue
        from violet_office import synthetic_abort as module
        _,proof=self.evidence()
        ctx=multiprocessing.get_context('spawn'); ready=ctx.Event(); out=ctx.Queue()
        process=ctx.Process(target=review_disable_worker,args=(self.c.context,self.rep,self.supervisor.identity,ready,out))
        original=module.verify_envelope; checked=[]
        def verify_locked(envelope,secret,limit=16384):
            result=original(envelope,secret,limit)
            if 'intent' in result and not checked:
                checked.append(True); process.start()
                self.assertTrue(ready.wait(5))
                with self.assertRaises(queue.Empty): out.get(timeout=0.2)
            return result
        try:
            with patch.object(module,'verify_envelope',side_effect=verify_locked):
                receipt=self.c.release_abort(proof)
            self.assertEqual(receipt['outcome'],'abort_verified')
            self.assertEqual(out.get(timeout=10),'disabled')
            process.join(5); self.assertEqual(process.exitcode,0)
            with self.assertRaises(Denied): self.c.release_abort(proof)
            self.c.complete_stop(self.rep)
        finally:
            if process.pid is not None and process.is_alive(): process.terminate(); process.join(5)
            out.close(); out.join_thread()

    def test_review_valid_mac_cannot_change_target_inventory_or_scope(self):
        job,proof=self.evidence()
        for key in ('scope_hash','inventory_hash','worker_nonce','job','supervisor_boot'):
            body=json.loads(canonical(proof['body']))
            body['intent']['managed'][key]='OTHER'
            altered={'body':body,'mac':mac(self.supervisor._secret,body)}
            with self.subTest(key=key),self.assertRaises(Denied): self.c.release_abort(altered)
        self.assertIn(job.token,self.pending()); self.cannot_complete()

    def test_review_external_journal_write_failure_retains_reservation(self):
        from violet_office import synthetic_abort as module
        job,proof=self.evidence()
        with patch.object(module,'insert_record',side_effect=sqlite3.OperationalError('Synthetic external write failure')):
            with self.assertRaises(Denied): self.c.release_abort(proof)
        self.assertIn(job.token,self.pending())
        with self.assertRaises(Denied): self.c.complete_stop(self.rep)

    def test_review_leaked_signing_key_defeats_mock_observation_trust_boundary(self):
        # NEGATIVE ASSURANCE: fixture key files are not OS-isolated. A trusted
        # signer compromise can forge complete observations; this is NOT secure
        # against malicious same-user workers and MUST NOT be a production gate.
        from violet_office.synthetic_abort import key_path
        from violet_office.synthetic_recovery import digest
        job=self.start('pause_flush')
        intent=self.c.checked(lambda head:self.c.authority.request_abort(
            head,job.token,job.capability,self.supervisor.owner,job.operation))
        stolen=key_path(self.c.context,self.supervisor.identity).read_text()
        body={'intent':intent,'facts':sorted(FACTS),'observation_hash':digest('FORGED-OBSERVATION'),
              'prior_output':'possible_or_unknown'}
        self.assertTrue(job.process.is_alive())
        self.c.release_abort({'body':body,'mac':mac(stolen,body)})
        self.c.complete_stop(self.rep)
        self.assertEqual(self.c.status()['mode'],'closed')
        # The untrusted holder of the key can falsely claim that the worker died.
        self.assertTrue(job.process.is_alive())
