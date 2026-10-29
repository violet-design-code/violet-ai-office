"""Policy transitions with real independent SQLite connections; synthetic DBs only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, Barrier
import sqlite3
import unittest
from unittest.mock import patch
from tests import test_accounting_access as fixtures
from violet_office.accounting_access import AccountingAccess
from violet_office.integrations.adapters import Principal
from violet_office.office import Office
from violet_office.store import Store, now


class PolicyTransitionTests(unittest.TestCase):
    setUp=fixtures.AccountingTests.setUp
    tearDown=fixtures.AccountingTests.tearDown
    snapshot=fixtures.AccountingTests.snapshot

    def pending(self):
        return fixtures.AccountingTests.policy(self,'AI27','detail',approve=False)

    def state(self,p):
        return self.st.db.execute('SELECT status FROM acct_policies WHERE id=?',(p,)).fetchone()[0]

    def actions(self,p):
        return [r[0] for r in self.st.db.execute('SELECT action FROM acct_events WHERE policy_id=? ORDER BY rowid',(p,))]

    def deny_read(self,p):
        with self.assertRaises(PermissionError):
            self.s.read(self.owner,self.tasks['BIZ-A','AI27'],p,'detail',['amount_yen'],self.start,self.end)

    def test_pending_to_approved_is_atomic_and_audited(self):
        p=self.pending();self.s.decide(self.owner,p,True)
        self.assertEqual(self.state(p),'approved')
        self.assertEqual(self.actions(p),['proposed','approved'])
        self.assertEqual(self.st.db.execute('SELECT approved_by FROM acct_policies WHERE id=?',(p,)).fetchone()[0],'owner')
        self.assertEqual(len(self.s.read(self.owner,self.tasks['BIZ-A','AI27'],p,'detail',['amount_yen'],self.start,self.end)['data']),2)

    def test_pending_to_revoked_is_atomic_and_audited(self):
        p=self.pending();self.s.revoke(self.owner,p)
        self.assertEqual(self.state(p),'revoked')
        self.assertEqual(self.actions(p),['proposed','revoked'])
        self.deny_read(p)

    def test_revoked_cannot_be_approved_and_denial_audit_survives(self):
        p=self.pending();self.s.revoke(self.owner,p)
        with self.assertRaises(ValueError):self.s.decide(self.owner,p,True)
        self.assertEqual(self.state(p),'revoked')
        self.assertEqual(self.actions(p)[-1],'approve_denied_state')
        self.assertNotIn('approved',self.actions(p))
        self.deny_read(p)

    def test_rejected_cannot_be_approved(self):
        p=self.pending();self.s.decide(self.owner,p,False)
        with self.assertRaises(ValueError):self.s.decide(self.owner,p,True)
        self.assertEqual(self.state(p),'rejected')
        self.assertEqual(self.actions(p)[-1],'approve_denied_state')
        self.deny_read(p)

    def test_explicit_expired_state_cannot_be_approved(self):
        p=self.pending();self.st.db.execute("UPDATE acct_policies SET status='expired' WHERE id=?",(p,))
        with self.assertRaises(ValueError):self.s.decide(self.owner,p,True)
        self.assertEqual(self.state(p),'expired')
        self.assertEqual(self.actions(p)[-1],'approve_denied_state')
        self.deny_read(p)

    def test_expired_timestamp_cannot_be_approved(self):
        p=self.pending();self.st.db.execute('UPDATE acct_policies SET expires_at=? WHERE id=?',((now()-timedelta(seconds=1)).isoformat(),p))
        with self.assertRaises(ValueError):self.s.decide(self.owner,p,True)
        self.assertEqual(self.state(p),'pending')
        self.assertEqual(self.actions(p)[-1],'approve_denied_expired')
        self.deny_read(p)

    def test_duplicate_approval_is_rejected_without_duplicate_success(self):
        p=self.pending();self.s.decide(self.owner,p,True)
        with self.assertRaises(ValueError):self.s.decide(self.owner,p,True)
        self.assertEqual(self.state(p),'approved')
        self.assertEqual(self.actions(p).count('approved'),1)
        self.assertEqual(self.actions(p)[-1],'approve_denied_state')

    def test_duplicate_revocation_is_rejected_without_duplicate_success(self):
        p=self.pending();self.s.revoke(self.owner,p)
        with self.assertRaises(ValueError):self.s.revoke(self.owner,p)
        self.assertEqual(self.state(p),'revoked')
        self.assertEqual(self.actions(p).count('revoked'),1)
        self.assertEqual(self.actions(p)[-1],'revoke_denied_state')
        self.deny_read(p)

    def test_approved_to_revoked_preserves_approver_and_denies_read(self):
        p=self.pending();self.s.decide(self.owner,p,True);self.s.revoke(self.owner,p)
        self.assertEqual(self.state(p),'revoked')
        self.assertEqual(self.st.db.execute('SELECT approved_by FROM acct_policies WHERE id=?',(p,)).fetchone()[0],'owner')
        self.deny_read(p)

    def race(self,first,second):
        """Both services are created in their own threads before the race starts."""
        p=self.pending()
        path=self.st.db.execute('PRAGMA database_list').fetchone()[2]
        ready=Barrier(3);go_first=Event();go_second=Event();locked=Event();release=Event();attempted=Event();done_second=Event()
        def worker(action,is_first):
            st=Store(path)
            try:
                service=AccountingAccess(Office(st))
                original=service.owner
                held=False
                def owner(principal,business=None):
                    nonlocal held
                    original(principal,business)
                    if is_first and business is not None and not held:
                        held=True;locked.set()
                        if not release.wait(5):raise AssertionError('Test release timeout')
                service.owner=owner
                ready.wait(5)
                if not (go_first if is_first else go_second).wait(5):raise AssertionError('Test start timeout')
                if not is_first:attempted.set()
                try:
                    if action=='revoke':service.revoke(self.owner,p)
                    else:service.decide(self.owner,p,True)
                    return 'success'
                except ValueError:
                    return 'denied'
                finally:
                    if not is_first:done_second.set()
            finally:st.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(worker,first,True);b=pool.submit(worker,second,False)
            try:
                ready.wait(5);go_first.set()
                self.assertTrue(locked.wait(5))
                go_second.set();self.assertTrue(attempted.wait(5))
                # The second transaction cannot finish while the first writer holds its lock.
                self.assertFalse(done_second.wait(0.05))
            finally:
                release.set();go_first.set();go_second.set()
            return p,a.result(timeout=5),b.result(timeout=5)

    def test_two_connections_revoke_first_then_stale_approval_is_denied(self):
        p,revoked,approved=self.race('revoke','approve')
        self.assertEqual((revoked,approved),('success','denied'))
        self.assertEqual(self.state(p),'revoked')
        self.assertEqual(self.actions(p)[-2:],['revoked','approve_denied_state'])
        self.deny_read(p)

    def test_two_connections_approve_first_then_revoke_finishes_revoked(self):
        p,approved,revoked=self.race('approve','revoke')
        self.assertEqual((approved,revoked),('success','success'))
        self.assertEqual(self.state(p),'revoked')
        self.assertEqual(self.actions(p)[-2:],['approved','revoked'])
        self.deny_read(p)

    def test_two_connections_duplicate_approval_only_one_succeeds(self):
        p,a,b=self.race('approve','approve')
        self.assertEqual((a,b),('success','denied'))
        self.assertEqual(self.state(p),'approved')
        self.assertEqual(self.actions(p).count('approved'),1)

    def test_two_connections_duplicate_revocation_only_one_succeeds(self):
        p,a,b=self.race('revoke','revoke')
        self.assertEqual((a,b),('success','denied'))
        self.assertEqual(self.state(p),'revoked')
        self.assertEqual(self.actions(p).count('revoked'),1)
        self.deny_read(p)

    def test_reported_interleaving_cannot_commit_revoke_inside_approval_window(self):
        p=self.pending();path=self.st.db.execute('PRAGMA database_list').fetchone()[2]
        other_store=Store(path)
        try:
            other=AccountingAccess(Office(other_store));other_store.db.execute('PRAGMA busy_timeout=0')
            original=self.s.owner
            observed=[]
            def interleave(principal,business=None):
                original(principal,business)
                if business is None:
                    return
                # This is the owner check AFTER the pending row was read in the reported bug.
                try:other.revoke(self.owner,p)
                except PermissionError:
                    observed.append('writer_lock_denied')
                    raise
            with patch.object(self.s,'owner',side_effect=interleave):
                with self.assertRaises(PermissionError):self.s.decide(self.owner,p,True)
            self.assertEqual(observed,['writer_lock_denied'])
            self.assertEqual(self.state(p),'pending')
            other.revoke(self.owner,p)
            with self.assertRaises(ValueError):self.s.decide(self.owner,p,True)
            self.assertEqual(self.state(p),'revoked')
            self.assertNotIn('approved',self.actions(p))
            self.deny_read(p)
        finally:other_store.close()

    def test_compare_and_set_zero_rows_is_denied_and_audited(self):
        p=self.pending()
        self.st.db.execute("CREATE TRIGGER synthetic_skip BEFORE UPDATE OF status ON acct_policies BEGIN SELECT RAISE(IGNORE); END")
        for operation in (lambda:self.s.decide(self.owner,p,True),lambda:self.s.revoke(self.owner,p)):
            with self.assertRaises(ValueError):operation()
        self.assertEqual(self.state(p),'pending')
        self.assertEqual(self.actions(p)[-2:],['approve_denied_conflict','revoke_denied_conflict'])
        self.deny_read(p)

    def test_sqlite_deadline_guard_refuses_expiry_after_python_check(self):
        p=self.pending();past=now()-timedelta(seconds=1)
        self.st.db.execute('UPDATE acct_policies SET expires_at=? WHERE id=?',(past.isoformat(),p))
        # Simulate the preliminary Python clock/check being stale, while SQLite time is live.
        with patch('violet_office.accounting_access.expiry',return_value=now()+timedelta(days=1)):
            with self.assertRaises(ValueError):self.s.decide(self.owner,p,True)
        self.assertEqual(self.state(p),'pending')
        self.assertEqual(self.actions(p)[-1],'approve_denied_conflict')
        self.deny_read(p)

    def test_audit_failure_rolls_back_approval_and_revocation(self):
        for approved in (False,True):
            p=self.pending()
            if approved:self.s.decide(self.owner,p,True)
            before=self.state(p)
            with patch.object(self.s,'event',side_effect=sqlite3.OperationalError('synthetic audit unavailable')):
                with self.assertRaises(PermissionError):
                    if approved:self.s.revoke(self.owner,p)
                    else:self.s.decide(self.owner,p,True)
            self.assertEqual(self.state(p),before)
            self.assertFalse(self.st.db.in_transaction)

    def test_nested_call_is_refused_without_partial_transition(self):
        p=self.pending()
        self.st.db.execute('BEGIN IMMEDIATE')
        try:
            with self.assertRaises(PermissionError):self.s.decide(self.owner,p,True)
            with self.assertRaises(PermissionError):self.s.revoke(self.owner,p)
            self.assertEqual(self.state(p),'pending')
        finally:self.st.db.execute('ROLLBACK')
        self.assertEqual(self.state(p),'pending')
        self.deny_read(p)

    def test_nonrepresentative_cannot_approve_or_revoke(self):
        p=self.pending();viewer=Principal('viewer','viewer')
        with self.assertRaises(PermissionError):self.s.decide(viewer,p,True)
        with self.assertRaises(PermissionError):self.s.revoke(viewer,p)
        self.assertEqual(self.state(p),'pending')

    def test_invalid_expiry_and_nonboolean_decision_deny_without_echoing(self):
        p=self.pending()
        with self.assertRaises(ValueError):self.s.decide(self.owner,p,1)
        self.st.db.execute('UPDATE acct_policies SET expires_at=? WHERE id=?',('PRIVATE-SYNTHETIC-VALUE',p))
        with self.assertRaises(ValueError) as error:self.s.decide(self.owner,p,True)
        self.assertNotIn('PRIVATE-SYNTHETIC-VALUE',str(error.exception))
        self.assertEqual(self.state(p),'pending')
        self.assertEqual(self.actions(p)[-1],'approve_denied_expiry')

    def test_transition_does_not_change_assignments_or_accounting_rows(self):
        before=self.snapshot()
        p=self.pending();self.s.decide(self.owner,p,True);self.s.revoke(self.owner,p)
        self.assertEqual(self.snapshot(),before)
