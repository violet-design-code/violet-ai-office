"""Isolated D prototype tests; fresh temporary files and local socketpair only."""
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import multiprocessing
import socket
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

from violet_office.accounting_access import FIELDS, ROLES
from violet_office.synthetic_recovery import (BufferSink, DeliveryBroker, Denied, HttpSink,
    LocalLedger, MockAuthority, Scope, SyntheticRealm, Unavailable, canonical, mac, read_records, verify)


def copy_image(path):
    db = sqlite3.connect(':memory:')
    with closing(sqlite3.connect(path)) as source:
        source.backup(db)
    return db


def restore_image(image, path):
    # Only freshly generated temporary realm paths are passed by these tests.
    path.unlink()
    with closing(sqlite3.connect(path)) as target:
        image.backup(target)


def make_scope(agent='AI27', section='detail', business='BIZ-A', fields=('amount_yen',), task=None):
    return Scope(business,agent,task or 'TASK-'+business+'-'+agent,section,tuple(fields),
                 '2026-01-01','2026-02-01','PURPOSE-A')


def proposal_worker(context, representative, index, ready, go, out):
    try:
        ready.put(index)
        if not go.wait(15):
            raise RuntimeError('Synthetic start timeout')
        client = LocalLedger(context)
        for n in range(3):
            client.propose(representative,'P-'+str(index)+'-'+str(n),make_scope(),
                           (datetime.now(timezone.utc)+timedelta(days=1)).isoformat())
        out.put('ok')
    except Exception as error:
        out.put(type(error).__name__)


class BlockingStream:
    def __init__(self, entered, release):
        self.entered,self.release = entered,release
        self.buffer = BytesIO()

    def write(self, data):
        self.entered.set()
        if not self.release.wait(15):
            raise RuntimeError('Synthetic output timeout')
        return self.buffer.write(data)

    def flush(self):
        pass


def http_writer(context, policy, scope, entered, release, out):
    try:
        stream = BlockingStream(entered,release)
        result = DeliveryBroker(LocalLedger(context),lambda p,s:True).deliver(
            policy,scope,lambda s:[{'amount_yen':10}],HttpSink(stream))
        out.put((result['outcome'],stream.buffer.getvalue().startswith(b'HTTP/1.1 200 OK')))
    except Exception as error:
        out.put(type(error).__name__)


def approval_worker(context, representative, policy, action, ready, go, out):
    try:
        ready.put(action)
        if not go.wait(15):
            raise RuntimeError('Synthetic start timeout')
        getattr(LocalLedger(context),action)(representative,policy)
        out.put((action,'ok'))
    except Denied:
        out.put((action,'denied'))
    except Exception as error:
        out.put((action,type(error).__name__))


class SyntheticDTests(unittest.TestCase):
    def setUp(self):
        self.realm = SyntheticRealm()
        self.c = self.realm.client()
        self.rep = self.realm.representative_token
        self.scope = make_scope()
        self.expires = (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        self.broker = DeliveryBroker(self.c,lambda p,s:True)

    def tearDown(self):
        self.realm.close()

    def initialize(self):
        self.c.enroll(self.rep)
        self.c.open_after_review(self.rep,history_confirmed=True)

    def approved(self, scope=None, policy=None):
        scope = scope or self.scope; policy = policy or uuid4().hex
        self.c.propose(self.rep,policy,scope,self.expires)
        self.c.approve(self.rep,policy)
        return policy

    def deliver(self, policy, scope=None, fetch=None, broker=None, kind='artifact'):
        scope = scope or self.scope; stream = BytesIO()
        result = (broker or self.broker).deliver(policy,scope,fetch or (lambda s:[{'amount_yen':10}]),BufferSink(stream,kind))
        self.assertNotIn('data',result)
        return stream.getvalue()

    def policies(self):
        return self.c.authority._op('inspect_synthetic',lambda s:s['policies'])

    def test_explicit_enrollment_starts_closed_and_existing_approval_is_quarantined(self):
        self.c.enroll(self.rep,legacy_ids=['OLD'])
        self.assertEqual(self.c.status()['mode'],'closed')
        self.c.open_after_review(self.rep,history_confirmed=True)
        with self.assertRaises(Denied): self.deliver('OLD')
        with self.assertRaises(Denied): self.c.approve(self.rep,'OLD')
        self.assertEqual(self.policies()['OLD']['status'],'quarantined')
        with self.assertRaises(Denied): self.c.enroll(self.rep)

    def test_unregistered_denies_before_financial_fetch(self):
        fetched = []
        with self.assertRaises(Denied): self.deliver('UNKNOWN',fetch=lambda s:fetched.append(True))
        self.assertEqual(fetched,[])
        with self.assertRaises(Denied): self.c.status()

    def test_representative_proof_and_history_review_are_required(self):
        with self.assertRaises(Denied): self.c.enroll('representative')
        self.c.enroll(self.rep)
        with self.assertRaises(Denied): self.c.open_after_review(self.rep,history_confirmed=False)
        with self.assertRaises(Denied): self.c.open_after_review('viewer',history_confirmed=True)
        self.assertEqual(self.c.status()['mode'],'closed')

    def test_pending_approved_revoked_and_duplicate_transitions(self):
        self.initialize(); policy = 'POLICY-A'
        self.c.propose(self.rep,policy,self.scope,self.expires)
        with self.assertRaises(Denied): self.deliver(policy)
        self.c.approve(self.rep,policy)
        self.assertEqual(json.loads(self.deliver(policy)),[{'amount_yen':10}])
        with self.assertRaises(Denied): self.c.approve(self.rep,policy)
        self.c.revoke(self.rep,policy); self.c.complete_revocation(self.rep,policy)
        with self.assertRaises(Denied): self.deliver(policy)
        with self.assertRaises(Denied): self.c.approve(self.rep,policy)
        with self.assertRaises(Denied): self.c.revoke(self.rep,policy)
        with self.assertRaises(Denied): self.c.propose(self.rep,policy,self.scope,self.expires)

    def test_old_local_ledger_with_valid_authentication_is_refused(self):
        self.initialize(); p = self.approved()
        image = copy_image(self.c.context.local)
        try:
            self.c.revoke(self.rep,p)
            restore_image(image,self.c.context.local)
            # Historical HMACs are still authentic; freshness fails at external head.
            with closing(sqlite3.connect(self.c.context.local)) as db:
                records = read_records(db,self.c.context.local_key)
                self.assertTrue(verify(records,self.c.context.local_key)[0])
            with self.assertRaises(Denied): self.deliver(p)
            with self.assertRaises(Denied): self.c.status()
        finally: image.close()

    def test_old_mock_external_state_alone_is_refused(self):
        self.initialize(); p = self.approved()
        image = copy_image(self.c.context.external)
        try:
            self.c.revoke(self.rep,p)
            restore_image(image,self.c.context.external)
            with self.assertRaises(Denied): self.deliver(p)
        finally: image.close()

    def test_both_states_rolled_back_demonstrates_mock_trust_limit(self):
        self.initialize(); p = self.approved()
        local,external = copy_image(self.c.context.local),copy_image(self.c.context.external)
        try:
            self.c.revoke(self.rep,p)
            with self.assertRaises(Denied): self.deliver(p)
            restore_image(local,self.c.context.local); restore_image(external,self.c.context.external)
            # Deliberate LIMIT demonstration. A fake authority rolled back with its
            # ledger has no third anchor and will incorrectly accept historic approval.
            self.assertTrue(self.deliver(p))
        finally: local.close(); external.close()

    def test_old_authentic_signed_response_cannot_answer_a_new_nonce(self):
        self.initialize(); p = self.approved()
        old = self.c.authority.attest('OLD-NONCE')
        self.assertEqual(old['mac'],mac(self.c.context.external_key,old['body']))
        with patch.object(self.c.authority,'attest',return_value=old):
            with self.assertRaises(Denied): self.deliver(p)

    def test_bad_signature_and_unknown_instance_are_refused(self):
        self.initialize(); p = self.approved()
        original = self.c.authority.attest
        for change in ('mac','instance'):
            def bad(nonce, checkpoint):
                result = original(nonce,checkpoint)
                if change == 'mac': result['mac'] = '0'*64
                else:
                    result['body']['instance'] = 'OTHER'
                    result['mac'] = mac(self.c.context.external_key,result['body'])
                return result
            with self.subTest(change=change),patch.object(self.c.authority,'attest',side_effect=bad):
                with self.assertRaises(Denied): self.deliver(p)

    def test_mock_external_stopped_or_deleted_denies_without_recreating(self):
        self.initialize(); p = self.approved()
        with patch.object(self.c.authority,'attest',side_effect=Denied('Mock unavailable')):
            with self.assertRaises(Denied): self.deliver(p)
        self.c.context.external.unlink()
        with self.assertRaises(Denied): self.deliver(p)
        self.assertFalse(self.c.context.external.exists())

    def test_local_disappearance_is_not_new_registration(self):
        self.initialize(); self.c.context.local.unlink()
        with self.assertRaises(Denied): self.c.enroll(self.rep)
        with self.assertRaises(Denied): self.c.status()
        self.assertFalse(self.c.context.local.exists())

    def test_corrupt_databases_and_history_tampering_are_refused(self):
        self.initialize(); p = self.approved()
        for path in (self.c.context.local,self.c.context.external):
            image = copy_image(path)
            try:
                path.write_bytes(b'synthetic-corruption')
                with self.assertRaises(Denied): self.deliver(p)
                restore_image(image,path)
                with closing(sqlite3.connect(path)) as db:
                    db.execute('DELETE FROM journal WHERE sequence=(SELECT MAX(sequence) FROM journal)'); db.commit()
                with self.assertRaises(Denied): self.deliver(p)
            finally: restore_image(image,path); image.close()

    def test_external_commit_then_local_failure_leaves_mismatch_and_denies(self):
        self.initialize(); p = self.approved()
        with patch('violet_office.synthetic_recovery.insert_record') as insert:
            # External commits use a separate connection; fail ONLY the local write.
            def fail_local(db,item):
                path = db.execute('PRAGMA database_list').fetchone()[2]
                if path == str(self.c.context.local): raise sqlite3.OperationalError('synthetic-local-write-failure')
                db.execute('INSERT INTO journal VALUES(?,?)',(item['sequence'],canonical(item)))
            insert.side_effect = fail_local
            with self.assertRaises(Denied): self.c.revoke(self.rep,p)
        self.assertEqual(self.policies()[p]['status'],'revoked')
        with self.assertRaises(Denied): self.deliver(p)

    def test_external_update_failure_does_not_advance_local_history(self):
        self.initialize(); p = self.approved(); before = self.c.status()
        with patch.object(self.c.authority,'apply',side_effect=Unavailable('Mock write failure')):
            with self.assertRaises(Denied): self.c.revoke(self.rep,p)
        with self.assertRaises(Denied): self.c.status()
        with self.assertRaises(Denied): self.deliver(p)
        # The operation failed; stored approval is not silently changed or reused.
        self.assertEqual(self.policies()[p]['status'],'approved')

    def test_missing_confirmation_is_not_treated_as_last_safe_approval(self):
        self.initialize(); p = self.approved()
        with patch.object(self.c.authority,'confirm',side_effect=Denied('Mock confirmation lost')):
            with self.assertRaises(Denied): self.c.revoke(self.rep,p)
        with self.assertRaises(Denied): self.c.status()
        with self.assertRaises(Denied): self.deliver(p)

    def test_new_generation_requires_reconciliation_and_new_individual_policy(self):
        self.initialize(); old = self.approved(); ref = self.broker.issue_reference(old,self.scope)
        self.c.request_stop(self.rep); self.c.complete_stop(self.rep)
        with self.assertRaises(Denied): self.c.new_recovery_generation(self.rep,history_confirmed=False)
        epoch = self.c.status()['epoch']
        self.c.new_recovery_generation(self.rep,history_confirmed=True)
        self.assertEqual(self.c.status()['epoch'],epoch+1)
        self.c.open_after_review(self.rep,history_confirmed=True)
        with self.assertRaises(Denied): self.deliver(old)
        with self.assertRaises(Denied): self.c.approve(self.rep,old)
        fresh = self.approved()
        self.assertTrue(self.deliver(fresh))
        with self.assertRaises(Denied): self.broker.receive_reference(ref,fresh,self.scope,lambda s:[],BufferSink(BytesIO(),'handoff'))

    def test_scope_business_fields_period_task_and_purpose_are_bounded(self):
        self.initialize(); p = self.approved()
        for scope in (replace(self.scope,business='BIZ-B'),replace(self.scope,task='OTHER'),
                      replace(self.scope,fields=('day',)),replace(self.scope,start='2025-01-01'),
                      replace(self.scope,purpose_id='OTHER')):
            with self.subTest(scope=scope),self.assertRaises(Denied): self.deliver(p,scope)

    def test_ai04_is_denied_before_fetch_even_with_representative(self):
        self.initialize(); fetched = []
        bad = replace(self.scope,agent='AI04')
        with self.assertRaises(Denied): self.approved(bad)
        with self.assertRaises(Denied): self.deliver('MISSING',bad,lambda s:fetched.append(True))
        self.assertEqual(fetched,[])

    def test_all_28_roles_only_approved_fields_are_delivered(self):
        self.initialize()
        for number in range(1,29):
            agent = 'AI'+str(number).zfill(2)
            for section in FIELDS:
                scope = make_scope(agent,section,fields=tuple(sorted(FIELDS[section])))
                with self.subTest(agent=agent,section=section):
                    if section not in ROLES.get(agent,set()):
                        with self.assertRaises(Denied): self.approved(scope)
                        continue
                    p = self.approved(scope)
                    values = {f:'2026-01-05' if f == 'day' else 'revenue' if f == 'kind' else 10 for f in scope.fields}
                    result = json.loads(self.deliver(p,scope,lambda s:values))
                    self.assertEqual(result,values)

    def test_existing_normal_authorization_is_required_before_fetch_and_again_before_output(self):
        self.initialize(); p = self.approved(); fetched = []
        broker = DeliveryBroker(self.c,lambda p,s:False)
        with self.assertRaises(Denied): self.deliver(p,fetch=lambda s:fetched.append(True),broker=broker)
        self.assertEqual(fetched,[])
        calls = []
        def ordinary(policy,scope):
            calls.append(True); return len(calls) == 1
        broker = DeliveryBroker(self.c,ordinary)
        with self.assertRaises(Denied): self.deliver(p,broker=broker)
        self.assertEqual(len(calls),2)

    def test_projection_rejects_unclassified_text_and_unknown_fields(self):
        self.initialize(); p = self.approved()
        for data in ({'amount_yen':10,'customer':'synthetic-secret'}, {'amount_yen':'synthetic-secret'},
                     {'amount_yen':float('nan')}):
            with self.subTest(data=data),self.assertRaises(Denied): self.deliver(p,fetch=lambda s:data)
        for path in (self.c.context.local,self.c.context.external):
            with closing(sqlite3.connect(path)) as db:
                self.assertNotIn('synthetic-secret',str(db.execute('SELECT body FROM journal').fetchall()))

    def test_stop_while_fetching_discards_private_result_before_any_output(self):
        self.initialize(); p = self.approved(); sink = BytesIO()
        def fetch(scope):
            self.c.request_stop(self.rep); self.c.complete_stop(self.rep)
            return [{'amount_yen':10}]
        with self.assertRaises(Denied): self.broker.deliver(p,self.scope,fetch,HttpSink(sink))
        self.assertEqual(sink.getvalue(),b'')

    def test_http_bytes_and_flush_are_inside_delivery_reservation(self):
        self.initialize(); p = self.approved(); client = self.c; representative = self.rep
        class Stream(BytesIO):
            def flush(inner):
                client.request_stop(representative)
                with self.assertRaises(Denied): client.complete_stop(representative)
        stream = Stream()
        self.broker.deliver(p,self.scope,lambda s:[{'amount_yen':10}],HttpSink(stream))
        self.c.complete_stop(self.rep)
        self.assertTrue(stream.getvalue().startswith(b'HTTP/1.1 200 OK'))
        with self.assertRaises(Denied): self.deliver(p)

    def test_socketpair_http_handoff_is_local_and_valid(self):
        self.initialize(); p = self.approved()
        left,right = socket.socketpair()
        try:
            left.settimeout(3); right.settimeout(3)
            with left.makefile('wb',buffering=0) as stream:
                self.broker.deliver(p,self.scope,lambda s:[{'amount_yen':10}],HttpSink(stream))
            left.shutdown(socket.SHUT_WR)
            received = b''
            while True:
                chunk = right.recv(4096)
                if not chunk: break
                received += chunk
            headers,body = received.split(b'\r\n\r\n',1)
            self.assertIn(b'HTTP/1.1 200 OK',headers)
            self.assertEqual(json.loads(body),[{'amount_yen':10}])
        finally: left.close(); right.close()

    def test_partial_stream_writes_are_completed_and_broken_stream_is_unresolved(self):
        self.initialize(); p = self.approved()
        class Partial(BytesIO):
            def write(inner,data): return super().write(data[:3])
        stream = Partial()
        self.broker.deliver(p,self.scope,lambda s:[{'amount_yen':10}],BufferSink(stream))
        self.assertEqual(json.loads(stream.getvalue()),[{'amount_yen':10}])
        class Broken(BytesIO):
            def write(inner,data): raise OSError('synthetic write interrupted')
        with self.assertRaises(Denied): self.broker.deliver(p,self.scope,lambda s:[],HttpSink(Broken()))
        self.c.request_stop(self.rep)
        with self.assertRaises(Denied): self.c.complete_stop(self.rep)

    def test_failed_durable_finish_leaves_writer_unresolved_without_timeout_cleanup(self):
        self.initialize(); p = self.approved()
        with patch.object(self.c.authority,'finish',side_effect=Denied('Mock finish unavailable')):
            with self.assertRaises(Denied): self.deliver(p)
        self.c.request_stop(self.rep)
        for _ in range(3):
            with self.assertRaises(Denied): self.c.complete_stop(self.rep)
        self.assertEqual(self.c.status()['mode'],'draining')

    def test_revocation_during_output_blocks_new_reads_and_waits_for_writer(self):
        self.initialize(); p = self.approved(); client = self.c; representative = self.rep
        class Stream(BytesIO):
            def write(inner,data):
                client.revoke(representative,p)
                with self.assertRaises(Denied): client.complete_revocation(representative,p)
                with self.assertRaises(Denied): self.deliver(p)
                return super().write(data)
        self.broker.deliver(p,self.scope,lambda s:[{'amount_yen':10}],BufferSink(Stream()))
        self.c.complete_revocation(self.rep,p)
        with self.assertRaises(Denied): self.deliver(p)
        with self.assertRaises(Denied): self.c.approve(self.rep,p)

    def test_metadata_reference_rechecks_source_and_recipient(self):
        self.initialize(); source = self.approved()
        target = replace(self.scope,task='TARGET')
        recipient = self.approved(target)
        ref = self.broker.issue_reference(source,self.scope)
        sink = BytesIO()
        self.broker.receive_reference(ref,recipient,target,lambda s:[{'amount_yen':10}],BufferSink(sink,'handoff'))
        self.assertEqual(json.loads(sink.getvalue()),[{'amount_yen':10}])
        self.c.revoke(self.rep,source)
        with self.assertRaises(Denied): self.broker.receive_reference(ref,recipient,target,lambda s:[],BufferSink(BytesIO(),'handoff'))

    def test_reference_rejects_other_business_or_section_and_revoked_recipient(self):
        self.initialize(); source = self.approved(); ref = self.broker.issue_reference(source,self.scope)
        for target in (replace(self.scope,business='BIZ-B'),make_scope('AI02','summary',fields=('expense_yen',))):
            policy = self.approved(target)
            with self.assertRaises(Denied): self.broker.receive_reference(ref,policy,target,lambda s:[],BufferSink(BytesIO(),'handoff'))
        target = replace(self.scope,task='TARGET'); policy = self.approved(target)
        self.c.revoke(self.rep,policy)
        with self.assertRaises(Denied): self.broker.receive_reference(ref,policy,target,lambda s:[],BufferSink(BytesIO(),'handoff'))

    def test_multiple_process_updates_form_one_paired_history(self):
        self.initialize()
        ctx = multiprocessing.get_context('spawn'); ready,out = ctx.Queue(),ctx.Queue(); go = ctx.Event()
        workers = [ctx.Process(target=proposal_worker,args=(self.c.context,self.rep,i,ready,go,out)) for i in range(3)]
        try:
            for p in workers: p.start()
            for _ in workers: ready.get(timeout=15)
            go.set(); self.assertEqual([out.get(timeout=20) for _ in workers],['ok']*3)
            for p in workers: p.join(10); self.assertEqual(p.exitcode,0)
            self.assertEqual(self.c.status()['head'][0],11)
            with closing(sqlite3.connect(self.c.context.local)) as db:
                self.assertEqual([r['sequence'] for r in read_records(db,self.c.context.local_key)],list(range(1,12)))
        finally:
            go.set()
            for p in workers:
                if p.is_alive(): p.terminate(); p.join(5)
            ready.close(); out.close(); ready.join_thread(); out.join_thread()

    def test_stop_waits_for_http_writer_in_another_process(self):
        self.initialize(); policy = self.approved()
        ctx = multiprocessing.get_context('spawn'); entered,release = ctx.Event(),ctx.Event(); out = ctx.Queue()
        worker = ctx.Process(target=http_writer,args=(self.c.context,policy,self.scope,entered,release,out))
        try:
            worker.start(); self.assertTrue(entered.wait(15))
            self.c.request_stop(self.rep)
            with self.assertRaises(Denied): self.c.complete_stop(self.rep)
            with self.assertRaises(Denied): self.deliver(policy)
            release.set(); self.assertEqual(out.get(timeout=15),('handed_off',True))
            worker.join(10); self.assertEqual(worker.exitcode,0)
            self.c.complete_stop(self.rep)
            with self.assertRaises(Denied): self.deliver(policy)
        finally:
            release.set()
            if worker.is_alive(): worker.terminate(); worker.join(5)
            out.close(); out.join_thread()

    def test_two_process_approval_revocation_race_never_restores_revoked(self):
        self.initialize(); policy = 'RACE'; self.c.propose(self.rep,policy,self.scope,self.expires)
        ctx = multiprocessing.get_context('spawn'); ready,out = ctx.Queue(),ctx.Queue(); go = ctx.Event()
        workers = [ctx.Process(target=approval_worker,args=(self.c.context,self.rep,policy,action,ready,go,out))
                   for action in ('approve','revoke')]
        try:
            for p in workers: p.start()
            for _ in workers: ready.get(timeout=15)
            go.set(); results = dict(out.get(timeout=15) for _ in workers)
            for p in workers: p.join(10); self.assertEqual(p.exitcode,0)
            self.assertEqual(results['revoke'],'ok')
            self.assertIn(results['approve'],('ok','denied'))
            self.assertEqual(self.policies()[policy]['status'],'revoked')
            with self.assertRaises(Denied): self.deliver(policy)
        finally:
            go.set()
            for p in workers:
                if p.is_alive(): p.terminate(); p.join(5)
            ready.close(); out.close(); ready.join_thread(); out.join_thread()

    def test_transport_only_external_rollback_cannot_hide_an_active_writer(self):
        self.initialize(); p = self.approved(); image = copy_image(self.c.context.external)
        class Stream(BytesIO):
            def write(inner,data):
                # Roll back ONLY mock transport history after its permit was issued.
                restore_image(image,self.c.context.external)
                with self.assertRaises(Denied): self.c.request_stop(self.rep)
                with self.assertRaises(Denied): self.c.complete_stop(self.rep)
                return super().write(data)
        try:
            with self.assertRaises(Denied):
                self.broker.deliver(p,self.scope,lambda s:[{'amount_yen':10}],HttpSink(Stream()))
            with self.assertRaises(Denied): self.c.status()
        finally: image.close()

    def test_pinned_external_checkpoint_missing_or_tampered_is_refused(self):
        self.initialize(); p = self.approved(); image = copy_image(self.c.context.local)
        try:
            for sql in ("UPDATE authority_checkpoint SET body='{}'", 'DELETE FROM authority_checkpoint'):
                restore_image(image,self.c.context.local)
                with closing(sqlite3.connect(self.c.context.local)) as db:
                    db.execute(sql); db.commit()
                with self.assertRaises(Denied): self.deliver(p)
        finally: image.close()

    def test_expiry_and_unapproved_representative_cannot_grant_access(self):
        self.initialize(); p = self.approved(); real_datetime = datetime
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):
                return real_datetime.now(tz)+timedelta(days=2)
        with patch('violet_office.synthetic_recovery.datetime',Clock):
            with self.assertRaises(Denied): self.deliver(p)
        pending = 'UNAPPROVED'
        self.c.propose(self.rep,pending,self.scope,self.expires)
        with self.assertRaises(Denied): self.c.approve('viewer',pending)
        self.assertEqual(self.policies()[pending]['status'],'pending')

    def test_empty_scope_authorization_and_unknown_output_are_denied(self):
        self.initialize()
        with self.assertRaises(Denied): self.c.authorize([])
        p = self.approved()
        with self.assertRaises(Denied):
            self.c.checked(lambda h:self.c.authority.reserve(h,[(p,self.scope.data())],'OWNER','unknown'))

    def test_source_revocation_during_reference_fetch_prevents_handoff(self):
        self.initialize(); source = self.approved(); ref = self.broker.issue_reference(source,self.scope)
        target = replace(self.scope,task='TARGET'); recipient = self.approved(target); sink = BytesIO()
        def fetch(scope):
            self.c.revoke(self.rep,source)
            return [{'amount_yen':10}]
        with self.assertRaises(Denied):
            self.broker.receive_reference(ref,recipient,target,fetch,BufferSink(sink,'handoff'))
        self.assertEqual(sink.getvalue(),b'')

    def test_fetch_and_writer_exceptions_do_not_echo_sensitive_text(self):
        self.initialize(); p = self.approved()
        def fetch(scope):
            raise ValueError('synthetic-confidential-value')
        with self.assertRaises(Denied) as error: self.deliver(p,fetch=fetch)
        self.assertNotIn('synthetic-confidential-value',str(error.exception))
        class Stream(BytesIO):
            def write(inner,data): raise OSError('synthetic-confidential-value')
        with self.assertRaises(Denied) as error:
            self.broker.deliver(p,self.scope,lambda s:[{'amount_yen':10}],HttpSink(Stream()))
        self.assertNotIn('synthetic-confidential-value',str(error.exception))

    def test_real_existing_authorization_can_be_composed_in_synthetic_fixture_only(self):
        from tests import test_accounting_access as fixtures
        fixture = fixtures.AccountingTests(); fixture.setUp()
        try:
            self.initialize()
            policy = fixture.policy('AI27','detail',['amount_yen'])
            scope = replace(self.scope,task=fixture.tasks['BIZ-A','AI27'])
            self.approved(scope,policy)
            def normal(p,s):
                return fixture.s.authorize(fixture.owner,s.task,p,s.section,list(s.fields),s.start,s.end) == s.business
            broker = DeliveryBroker(self.c,normal)
            def fetch(s):
                return fixture.s.read(fixture.owner,s.task,policy,s.section,list(s.fields),s.start,s.end)['data']
            self.assertEqual(json.loads(self.deliver(policy,scope,fetch,broker)),[{'amount_yen':100},{'amount_yen':30}])
            fixture.st.db.execute("UPDATE bf_ai_assignments SET active=0 WHERE business_id='BIZ-A' AND agent='AI27'")
            with self.assertRaises(Denied): self.deliver(policy,scope,fetch,broker)
        finally: fixture.tearDown()
