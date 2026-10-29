"""Buffered output failure regressions; temporary realms only, no real network."""
from collections import deque
import gc
import io
import multiprocessing
import unittest
from unittest.mock import patch
import weakref

from tests import test_synthetic_recovery as fixtures
from violet_office.synthetic_recovery import (DeliveryBroker, Denied, HttpSink,
    LocalLedger, Unavailable)


class TransientRaw(io.RawIOBase):
    """A synchronous raw destination with a genuine BufferedWriter in front."""
    def __init__(self, steps=('fail',)):
        super().__init__()
        self.steps = deque(steps)
        self.received = bytearray()

    def writable(self):
        return True

    def write(self, data):
        if self.closed:
            raise ValueError('Synthetic raw destination closed')
        step = self.steps.popleft() if self.steps else len(data)
        if step == 'fail':
            raise OSError('SYNTHETIC-CONFIDENTIAL-ERROR')
        count = min(step,len(data))
        self.received.extend(data[:count])
        return count


def dispose_buffer(raw, stream):
    # Test cleanup closes the raw destination FIRST so buffered cleanup cannot
    # accidentally flush. This never acknowledges/releases a delivery reservation.
    raw.close()
    try:
        stream.close()
    except (OSError,ValueError):
        pass


def failed_buffer_worker(context, policy, scope, entered, proceed, messages, action):
    raw = TransientRaw(); stream = io.BufferedWriter(raw,buffer_size=8192)
    try:
        broker = DeliveryBroker(LocalLedger(context),lambda p,s:True)
        try:
            broker.deliver(policy,scope,lambda s:[{'amount_yen':10}],HttpSink(stream))
        except Denied:
            messages.put('unresolved')
        else:
            messages.put('unexpected_success'); return
        entered.set()
        if not proceed.wait(15):
            messages.put('timeout'); return
        if action == 'flush':
            stream.flush()
        else:
            stream.close()
        messages.put(('later_output',len(raw.received)>0))
    finally:
        dispose_buffer(raw,stream)


class OutputFailureTests(unittest.TestCase):
    setUp = fixtures.SyntheticDTests.setUp
    tearDown = fixtures.SyntheticDTests.tearDown
    initialize = fixtures.SyntheticDTests.initialize
    approved = fixtures.SyntheticDTests.approved
    deliver = fixtures.SyntheticDTests.deliver

    def pending(self):
        return self.c.authority._op('inspect_output_state',lambda s:list(s['permits'].values()))

    def failed_buffer(self, steps=('fail',)):
        self.initialize(); policy = self.approved()
        raw = TransientRaw(steps); stream = io.BufferedWriter(raw,buffer_size=8192)
        self.addCleanup(dispose_buffer,raw,stream)
        with self.assertRaises(Denied) as error:
            self.broker.deliver(policy,self.scope,lambda s:[{'amount_yen':10}],HttpSink(stream))
        self.assertNotIn('SYNTHETIC-CONFIDENTIAL-ERROR',str(error.exception))
        self.assertEqual(len(self.pending()),1)
        self.assertEqual(self.pending()[0]['phase'],'unresolved')
        return policy,raw,stream

    def assert_stop_unresolved(self):
        self.c.request_stop(self.rep)
        with self.assertRaises(Denied): self.c.complete_stop(self.rep)
        self.assertEqual(self.c.status()['mode'],'draining')
        self.assertEqual(len(self.pending()),1)

    def test_reported_buffered_flush_failure_cannot_complete_stop(self):
        policy,raw,stream = self.failed_buffer()
        self.assertEqual(raw.received,b'')
        self.assert_stop_unresolved()
        # Original exploit step: retrying flush can still hand off bytes, but STOP
        # HAS NOT COMPLETED. It must remain unresolved after that retry as well.
        stream.flush()
        self.assertTrue(raw.received.startswith(b'HTTP/1.1 200 OK'))
        self.assert_stop_unresolved()
        with self.assertRaises(Denied): self.deliver(policy)

    def test_close_after_failed_flush_does_not_release_reservation(self):
        _,raw,stream = self.failed_buffer()
        self.assert_stop_unresolved()
        stream.close()  # Standard close flushes retained bytes. This is not abort proof.
        self.assertTrue(stream.closed)
        self.assertTrue(raw.received.startswith(b'HTTP/1.1 200 OK'))
        self.assert_stop_unresolved()

    def test_repeated_flushes_and_broker_retry_do_not_confirm_failed_output(self):
        policy,raw,stream = self.failed_buffer()
        stream.flush(); before = bytes(raw.received)
        for _ in range(3):
            stream.flush()
            self.assertEqual(before,bytes(raw.received))
            with self.assertRaises(Denied): self.deliver(policy)
            self.assert_stop_unresolved()

    def test_partial_raw_write_then_exception_stays_unresolved(self):
        _,raw,stream = self.failed_buffer((7,'fail'))
        self.assertEqual(len(raw.received),7)
        self.assert_stop_unresolved()
        stream.flush()
        self.assertTrue(raw.received.startswith(b'HTTP/1.1 200 OK'))
        self.assert_stop_unresolved()

    def test_close_that_fails_while_flushing_stays_unresolved(self):
        _,raw,stream = self.failed_buffer(('fail','fail'))
        with self.assertRaises(OSError): stream.close()
        self.assertTrue(stream.closed)
        self.assertEqual(raw.received,b'')
        self.assert_stop_unresolved()

    def test_generic_abort_failure_cannot_be_treated_as_confirmation(self):
        self.initialize(); policy = self.approved()
        class Unconfirmed(io.BytesIO):
            def write(inner,data): raise OSError('SYNTHETIC-CONFIDENTIAL-ERROR')
            def abort(inner): raise OSError('Synthetic abort also failed')
        stream = Unconfirmed()
        with self.assertRaises(Denied):
            self.broker.deliver(policy,self.scope,lambda s:[],HttpSink(stream))
        with self.assertRaises(OSError): stream.abort()
        self.assert_stop_unresolved()

    def test_successful_generic_close_is_not_cancellation_evidence(self):
        self.initialize(); policy = self.approved()
        class Unknown(io.BytesIO):
            def flush(inner): raise OSError('Synthetic flush failed')
        stream = Unknown()
        with self.assertRaises(Denied):
            self.broker.deliver(policy,self.scope,lambda s:[],HttpSink(stream))
        stream.close()
        self.assertTrue(stream.closed)
        self.assert_stop_unresolved()

    def test_stop_during_write_then_flush_failure_cannot_be_acknowledged(self):
        self.initialize(); policy = self.approved(); client,rep = self.c,self.rep
        class StopDuringWrite(io.BytesIO):
            def write(inner,data):
                client.request_stop(rep)
                with self.assertRaises(Denied): client.complete_stop(rep)
                return super().write(data)
            def flush(inner): raise OSError('Synthetic flush failed')
        stream = StopDuringWrite()
        with self.assertRaises(Denied):
            self.broker.deliver(policy,self.scope,lambda s:[],HttpSink(stream))
        self.assert_stop_unresolved()

    def test_unresolved_audit_failure_does_not_delete_existing_permit(self):
        self.initialize(); policy = self.approved()
        class Broken(io.BytesIO):
            def write(inner,data): raise OSError('Synthetic write failed')
        with patch.object(self.c.authority,'mark_unresolved',side_effect=Unavailable('Mock audit unavailable')):
            with self.assertRaises(Denied):
                self.broker.deliver(policy,self.scope,lambda s:[],HttpSink(Broken()))
        self.assertEqual(len(self.pending()),1)
        self.assertEqual(self.pending()[0]['phase'],'emitting')
        self.assert_stop_unresolved()

    def test_failed_delivery_cannot_be_finished_by_either_old_abort_or_success_flag(self):
        self.initialize(); policy = self.approved(); guards = [(policy,self.scope.data())]
        token,proof = self.c.checked(lambda h:self.c.authority.reserve(h,guards,'OWNER','http'))
        self.c.checked(lambda h:self.c.authority.begin(h,token,proof,'OWNER'))
        self.c.checked(lambda h:self.c.authority.mark_unresolved(token,proof,'OWNER'))
        for outcome in ('aborted','handed_off'):
            with self.subTest(outcome=outcome),self.assertRaises(Denied):
                self.c.checked(lambda h:self.c.authority.finish(token,proof,'OWNER',outcome))
        self.assert_stop_unresolved()

    def test_successful_reservation_can_only_be_released_once(self):
        self.initialize(); policy = self.approved(); guards = [(policy,self.scope.data())]
        token,proof = self.c.checked(lambda h:self.c.authority.reserve(h,guards,'OWNER','http'))
        self.c.checked(lambda h:self.c.authority.begin(h,token,proof,'OWNER'))
        self.c.checked(lambda h:self.c.authority.finish(token,proof,'OWNER','handed_off'))
        with self.assertRaises(Denied):
            self.c.checked(lambda h:self.c.authority.finish(token,proof,'OWNER','handed_off'))
        self.assertEqual(self.pending(),[])
        self.c.request_stop(self.rep); self.c.complete_stop(self.rep)
        with self.assertRaises(Denied): self.deliver(policy)

    def test_keyboard_interrupt_never_runs_success_cleanup(self):
        self.initialize(); policy = self.approved()
        class Interrupted(io.BytesIO):
            def write(inner,data): raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.broker.deliver(policy,self.scope,lambda s:[],HttpSink(Interrupted()))
        self.assertEqual(self.pending()[0]['phase'],'unresolved')
        self.assert_stop_unresolved()

    def test_garbage_collection_never_releases_reservation(self):
        self.initialize(); policy = self.approved(); raw = TransientRaw()
        def temporary_output():
            stream = io.BufferedWriter(raw,buffer_size=8192)
            ref = weakref.ref(stream)
            try:
                self.broker.deliver(policy,self.scope,lambda s:[],HttpSink(stream))
            except Denied:
                pass
            return ref
        ref = temporary_output(); gc.collect()
        self.assertIsNone(ref())
        self.assertEqual(self.pending()[0]['phase'],'unresolved')
        self.assert_stop_unresolved()
        raw.close()

    def test_unresolved_audit_contains_only_fixed_status_and_metadata(self):
        _,_,_ = self.failed_buffer()
        # No actual stream/source data or exception strings belong in either DB.
        import sqlite3
        from contextlib import closing
        for path in (self.c.context.local,self.c.context.external):
            with closing(sqlite3.connect(path)) as db:
                text = str(db.execute('SELECT body FROM journal').fetchall())
            self.assertNotIn('SYNTHETIC-CONFIDENTIAL-ERROR',text)
            self.assertNotIn('HTTP/1.1',text)
            self.assertNotIn('"amount_yen":10',text)
        self.assert_stop_unresolved()

    def run_process_failure(self, action, terminate=False):
        self.initialize(); policy = self.approved()
        ctx = multiprocessing.get_context('spawn')
        entered,proceed = ctx.Event(),ctx.Event(); messages = ctx.Queue()
        worker = ctx.Process(target=failed_buffer_worker,
            args=(self.c.context,policy,self.scope,entered,proceed,messages,action))
        try:
            worker.start(); self.assertTrue(entered.wait(15))
            self.assertEqual(messages.get(timeout=15),'unresolved')
            self.assert_stop_unresolved()
            if terminate:
                worker.terminate(); worker.join(10)
                self.assertFalse(worker.is_alive())
            else:
                proceed.set()
                self.assertEqual(messages.get(timeout=15),('later_output',True))
                worker.join(10); self.assertEqual(worker.exitcode,0)
            # A child exiting/being killed, or its flush/close returning, is NOT a
            # trusted persistent abort confirmation. Parent still cannot complete.
            self.assert_stop_unresolved()
            with self.assertRaises(Denied): self.deliver(policy)
        finally:
            # A killed process can leave multiprocessing.Event internals locked.
            # Never reuse its shared synchronization objects after termination.
            if not terminate:
                proceed.set()
            if worker.is_alive(): worker.terminate(); worker.join(5)
            messages.close(); messages.join_thread()

    def test_other_process_stop_stays_pending_across_child_reflush_and_exit(self):
        self.run_process_failure('flush')

    def test_other_process_stop_stays_pending_across_child_close_and_exit(self):
        self.run_process_failure('close')

    def test_child_termination_is_not_automatic_abort_confirmation(self):
        self.run_process_failure('flush',terminate=True)
